"""Battle discovery, navy-out setup generation and result -> HOI4 writeback."""

from __future__ import annotations

import json
import os
import random
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from models import (
    BattleCandidate,
    BattleContext,
    BattleResult,
    Ship,
    ShipResult,
    WritebackPlan,
)
from designer import DesignerDB
from hoi4_map import Hoi4Map
from hoi4_save import SaveData
from ship_stats import (
    TYPE_NAME_CN,
    disambiguate_names,
    has_seaplane_modules,
    plane_unit,
    prepare_ship,
    recon_unit,
)


CAPITAL_TYPES = {"carrier", "battleship", "battlecruiser", "heavy_cruiser"}


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def date_day(value: str) -> int:
    """HOI4 date '1936.1.1.14' -> a monotonically increasing day number."""
    try:
        parts = [int(x) for x in value.replace('"', "").split(".")]
    except ValueError:
        return -1
    if len(parts) < 3:
        return -1
    y, m, d = parts[0], parts[1], parts[2]
    if y <= 1:
        return -1
    return y * 372 + m * 31 + d


def _recent(last_combat: str, save_date: str, days: int = 2) -> bool:
    a = date_day(last_combat)
    b = date_day(save_date)
    if a < 0 or b < 0:
        return False
    return 0 <= b - a <= days


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------

def _ship_damaged(ship: Ship) -> bool:
    if ship.strength < 99.5:
        return True
    if ship.max_org > 0 and ship.organisation < ship.max_org - 0.5:
        return True
    return False


def find_candidates(
    save: SaveData,
    mapdata: Hoi4Map,
    *,
    max_battles: int = 3,
) -> List[BattleCandidate]:
    """Find sea zones with player ships and at-war enemy ships.

    This is deliberately a spatial/at-war heuristic rather than a parser for
    the engine's transient naval-combat object: it survives save-format
    changes and covers battles that did not serialize a combat block yet.
    `last_combat_date`, damaged ships and any detected `naval_combat` province
    are used to mark a candidate as "active".
    """
    player = save.player_tag
    if not player:
        return []

    by_province_tag: Dict[Tuple[int, str], List] = defaultdict(list)
    for tf in save.task_forces:
        if not tf.tag or not tf.ships:
            continue
        if not mapdata.is_sea(tf.location):
            continue
        by_province_tag[(tf.location, tf.tag)].append(tf)

    provinces = sorted({p for p, _ in by_province_tag})
    candidates: List[BattleCandidate] = []
    for province in provinces:
        player_tfs = by_province_tag.get((province, player), [])
        if not player_tfs:
            continue
        player_ships = [s for tf in player_tfs for s in tf.ships if s.strength > 0]
        if not player_ships:
            continue

        enemy_tags = sorted(
            {
                tag
                for (p, tag) in by_province_tag
                if p == province and tag != player and save.at_war(player, tag)
            }
        )
        for enemy_tag in enemy_tags:
            enemy_tfs = by_province_tag.get((province, enemy_tag), [])
            enemy_ships = [s for tf in enemy_tfs for s in tf.ships if s.strength > 0]
            if not enemy_ships:
                continue

            info = mapdata.info(province)
            all_ships = player_ships + enemy_ships
            active = province in save.naval_combat_provinces
            last_combat = "1.1.1.1"
            for tf in player_tfs + enemy_tfs:
                if date_day(tf.last_combat_date) > date_day(last_combat):
                    last_combat = tf.last_combat_date
                if _recent(tf.last_combat_date, save.date):
                    active = True
            if any(_ship_damaged(s) for s in all_ships):
                active = True

            capitals = sum(1 for s in all_ships if s.definition in CAPITAL_TYPES or s.definition.replace("_", "") in CAPITAL_TYPES)
            score = len(all_ships) + 4 * capitals + (30.0 if active else 0.0)
            cand = BattleCandidate(
                province=province,
                player_tag=player,
                enemy_tag=enemy_tag,
                player_ships=player_ships,
                enemy_ships=enemy_ships,
                zone_name=mapdata.zone_name(province),
                region_name=info.region_name,
                naval_terrain=mapdata.terrain_cn(province),
                preset=info.preset,
                active=active,
                last_combat_date=last_combat,
                score=score,
            )
            cand.summary = cand.title()
            candidates.append(cand)

    candidates.sort(key=lambda c: (c.active, c.score), reverse=True)
    return candidates[:max_battles]


# ---------------------------------------------------------------------------
# navy-out setup
# ---------------------------------------------------------------------------

def _unit_entry(ship: Ship) -> Dict[str, object]:
    # Exact HOI4 structural points: current strength / designer max strength.
    max_hp = max(1.0, ship.max_strength)
    hp = max(0.0, min(ship.start_strength, max_hp))
    entry: Dict[str, object] = {
        "type": ship.navy_type,
        "name": ship.display_name or ship.name,
        "hp": round(hp, 1),
        "max_hp": round(max_hp, 1),
        "organization": round(ship.start_org, 1),
        "max_organization": round(ship.max_org, 1),
    }
    if ship.attributes:
        entry["attributes"] = {k: round(v, 2) for k, v in ship.attributes.items()}
    return entry


def _side_units(
    ships: List[Ship],
    save: SaveData,
    designer: DesignerDB,
    *,
    with_carrier_air: bool,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    units = [_unit_entry(s) for s in ships]
    aircraft: List[Dict[str, object]] = []
    for ship in ships:
        # Carrier: its real air wings plus 3 dedicated naval recon aircraft.
        if ship.navy_type == "carrier":
            if with_carrier_air:
                for wing in save.air_wings_for(ship.ship_id):
                    unit = plane_unit(wing, save, designer)
                    if unit is not None:
                        aircraft.append(unit)
            aircraft.extend(recon_unit() for _ in range(3))
        # Seaplane catapult / seaplane tender: one abstract recon aircraft
        # per ship (both modules on the same ship still count once).
        if has_seaplane_modules(save.modules_for_ship(ship).values()):
            aircraft.append(recon_unit())
    units.extend(aircraft)
    return units, aircraft


def build_context(
    battle: BattleCandidate,
    save: SaveData,
    mapdata: Hoi4Map,
    session_dir: str,
    designer: DesignerDB,
    *,
    with_carrier_air: bool = True,
) -> Tuple[BattleContext, Dict[str, object]]:
    for ship in battle.player_ships:
        prepare_ship(ship, save, designer)
    for ship in battle.enemy_ships:
        prepare_ship(ship, save, designer)
    disambiguate_names(battle.player_ships)
    disambiguate_names(battle.enemy_ships)

    player_units, player_air = _side_units(
        battle.player_ships, save, designer, with_carrier_air=with_carrier_air
    )
    enemy_units, enemy_air = _side_units(
        battle.enemy_ships, save, designer, with_carrier_air=with_carrier_air
    )

    seed = random.randint(1, 2_000_000_000)
    output_path = os.path.join(session_dir, "battle_result.txt")
    setup = {
        "map_preset": battle.preset,
        "weather": save.weather_for(battle.province),
        "seed": seed,
        "output_path": output_path.replace("\\", "/"),
        "player": {"units": player_units},
        "enemy": {"units": enemy_units},
    }
    context = BattleContext(
        battle=battle,
        player_tag=battle.player_tag,
        enemy_tag=battle.enemy_tag,
        province=battle.province,
        zone_name=battle.zone_name,
        naval_terrain=battle.naval_terrain,
        preset=battle.preset,
        player_ships=battle.player_ships,
        enemy_ships=battle.enemy_ships,
        player_aircraft=player_air,
        enemy_aircraft=enemy_air,
        setup_path=os.path.join(session_dir, "external_setup.json"),
        output_path=output_path,
        seed=seed,
        player_doctrine=save.doctrine_for(battle.player_tag),
        enemy_doctrine=save.doctrine_for(battle.enemy_tag),
        player_navy_techs=len(save.techs_for(battle.player_tag)),
        enemy_navy_techs=len(save.techs_for(battle.enemy_tag)),
    )
    return context, setup


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------

PLANE_TITLE_HINTS = ("战斗机", "海军轰炸机", "海上侦察机", "轰炸机", "攻击机", "侦察机")


def _is_aircraft_title(title: str) -> bool:
    # Do not use "#" here: unnamed ships use fallbacks such as
    # "destroyer #10", while aircraft titles contain one of the Chinese type
    # names ("战斗机 #13", "海军轰炸机 #5", ...).
    return any(hint in title for hint in PLANE_TITLE_HINTS)


RESULT_TYPE_TO_DEF = {
    "航空母舰": "carrier",
    "战列舰": "battleship",
    "战列巡洋舰": "battle_cruiser",
    "重型巡洋舰": "heavy_cruiser",
    "轻型巡洋舰": "light_cruiser",
    "驱逐舰": "destroyer",
    "潜艇": "submarine",
}


def _expected_title(ship: Ship) -> str:
    return f"{ship.display_name or ship.name}-{ship.navy_type_name}"


def _definition_from_title(title: str) -> str:
    """Infer a HOI4 ship definition from navy-out's `name-type` title."""
    clean = title.replace("（机库内）", "").strip()
    if "-" in clean:
        suffix = clean.rsplit("-", 1)[1].strip()
        if suffix in RESULT_TYPE_TO_DEF:
            return RESULT_TYPE_TO_DEF[suffix]
    for type_name, definition in RESULT_TYPE_TO_DEF.items():
        if type_name in clean:
            return definition
    return ""


def _find_ship(
    title: str,
    ships: List[Ship],
    by_title: Dict[str, Ship],
    used: set,
) -> Optional[Ship]:
    """Match a result title to an unused ship.

    Order: exact display title -> name prefix -> type suffix -> sole unused
    ship. The type/sole-ship fallbacks make writeback survive a changed ship
    name without silently dropping all losses.
    """
    title = title.strip()
    ship = by_title.get(title)
    if ship is not None and id(ship) not in used:
        return ship
    for candidate in ships:
        if id(candidate) in used:
            continue
        base = _expected_title(candidate)
        if title == base or title.startswith(base + " ") or title.startswith(base + "（"):
            return candidate
    if "-" in title:
        name = title.rsplit("-", 1)[0].strip()
        for candidate in ships:
            if id(candidate) in used:
                continue
            if name == (candidate.display_name or candidate.name):
                return candidate
    definition = _definition_from_title(title)
    if definition:
        for candidate in ships:
            if id(candidate) not in used and candidate.definition == definition:
                return candidate
    # 1v1 fallback: there is only one unmatched ship left.
    unmatched = [s for s in ships if id(s) not in used]
    if len(unmatched) == 1:
        return unmatched[0]
    return None


def _side_plan(
    ships: List[Ship],
    results: List[ShipResult],
    lost: List[str],
    retreated: List[str],
) -> Tuple[float, float, Dict[str, int], List[str], int, List[str], float, float]:
    """Return (org_loss_points, str_loss_points, sunk_by_type, warnings,
    survivors_count, matched_lost_names, survivor_org_denominator,
    survivor_str_denominator)."""
    by_title = {_expected_title(s): s for s in ships}
    warnings: List[str] = []
    org_loss = 0.0
    str_loss = 0.0
    sunk: Dict[str, int] = defaultdict(int)
    survivors = 0
    survivor_org_den = 0.0
    survivor_str_den = 0.0
    matched_lost: List[str] = []
    used = set()
    unmatched_lost_by_type: Dict[str, int] = defaultdict(int)

    # 1) Surviving ships: match by title/name/type/sole-unmatched fallback.
    for item in results:
        if item.status != "remaining" or _is_aircraft_title(item.title):
            continue
        ship = _find_ship(item.title, ships, by_title, used)
        if ship is None:
            warnings.append(f"结果中的舰船无法匹配：{item.title}")
            continue
        used.add(id(ship))
        if item.max_hp > 0:
            fraction = max(0.0, min(1.0, item.hp / item.max_hp))
            new_strength = fraction * ship.max_strength
        else:
            new_strength = ship.start_strength
        new_org = max(0.0, min(ship.max_org, item.org))
        org_loss += max(0.0, ship.start_org - new_org)
        str_loss += max(0.0, ship.start_strength - new_strength)
        survivor_org_den += ship.max_org
        survivor_str_den += ship.max_strength
        survivors += 1

    # 2) Explicit losses.
    for title in lost:
        if _is_aircraft_title(title):
            continue
        ship = _find_ship(title, ships, by_title, used)
        if ship is not None:
            used.add(id(ship))
            sunk[ship.definition] += 1
            matched_lost.append(ship.display_name or ship.name)
            continue
        definition = _definition_from_title(title)
        if definition and any(s.definition == definition for s in ships):
            sunk[definition] += 1
            unmatched_lost_by_type[definition] += 1
            matched_lost.append(title)
        else:
            warnings.append(f"损失列表中的舰船无法匹配：{title}")

    # 3) Retreated ships keep their current state (no HP/org report).
    for title in retreated:
        if _is_aircraft_title(title):
            continue
        ship = _find_ship(title, ships, by_title, used)
        if ship is not None:
            used.add(id(ship))
        else:
            warnings.append(f"撤离列表中的舰船无法匹配：{title}")

    # 4) Any initial ship not accounted for as surviving/lost/retreated is a
    # loss. This is the safety net when navy-out's title format changes.
    all_defs = {s.definition for s in ships}
    for definition in all_defs:
        initial = sum(1 for s in ships if s.definition == definition)
        consumed = sum(
            1 for s in ships if s.definition == definition and id(s) in used
        ) + unmatched_lost_by_type.get(definition, 0)
        missing = initial - consumed
        if missing > 0:
            sunk[definition] += missing
            warnings.append(f"{definition} 有 {missing} 艘未出现在结果中，按损失处理")

    return (
        org_loss,
        str_loss,
        dict(sunk),
        warnings,
        survivors,
        matched_lost,
        survivor_org_den,
        survivor_str_den,
    )


def _side_all_retreated(
    results: List[ShipResult], lost: List[str], retreated: List[str]
) -> bool:
    """True when every real ship on a side left by choice, not by damage."""
    ship_lost = [title for title in lost if not _is_aircraft_title(title)]
    ship_retreated = [title for title in retreated if not _is_aircraft_title(title)]
    if not ship_retreated or ship_lost:
        return False
    remaining = [
        item
        for item in results
        if item.status == "remaining" and not _is_aircraft_title(item.title)
    ]
    return not remaining


def build_writeback(
    context: BattleContext,
    result: BattleResult,
    retreat_action: str = "none",
) -> WritebackPlan:
    p_org, p_str, p_sunk, p_warn, p_surv, p_lost, p_org_den, p_str_den = _side_plan(
        context.player_ships, result.player, result.player_lost, result.player_retreated
    )
    e_org, e_str, e_sunk, e_warn, e_surv, e_lost, e_org_den, e_str_den = _side_plan(
        context.enemy_ships, result.enemy, result.enemy_lost, result.enemy_retreated
    )
    # If a result omits ships, the deficit safety net treats them as sunk.
    # That is not a clean retreat, so never suppress real losses.
    player_all_retreated = _side_all_retreated(
        result.player, result.player_lost, result.player_retreated
    ) and not any(p_sunk.values())
    enemy_all_retreated = _side_all_retreated(
        result.enemy, result.enemy_lost, result.enemy_retreated
    ) and not any(e_sunk.values())
    all_retreated = player_all_retreated and enemy_all_retreated

    # `damage_units` with ratio=yes is a fraction of each unit's MAXIMUM.
    # Survivors are the only ships left after destroy_ships, so the damage
    # ratio is normalised over the survivors' maxima.
    p_org_den = max(1.0, p_org_den)
    e_org_den = max(1.0, e_org_den)
    p_str_den = max(1.0, p_str_den)
    e_str_den = max(1.0, e_str_den)

    # If one side was fully wiped, force a full org/str damage line at the sea
    # province as well. destroy_ships is type/count based and can remove a
    # different hull elsewhere; the full damage guarantees the vanilla naval
    # combat in this province terminates instead of continuing forever.
    # A side that lost ships but has no survivors and no retreats is wiped.
    # Do not do this when the side merely retreated (that would damage ships
    # that already left the battle).
    if (
        p_surv == 0
        and sum(p_sunk.values()) > 0
        and not result.player_retreated
        and context.player_ships
    ):
        p_org, p_str, p_org_den, p_str_den = 1.0, 1.0, 1.0, 1.0
    if (
        e_surv == 0
        and sum(e_sunk.values()) > 0
        and not result.enemy_retreated
        and context.enemy_ships
    ):
        e_org, e_str, e_org_den, e_str_den = 1.0, 1.0, 1.0, 1.0

    # A clean retreat must not count as HP damage. HOI4, however, has no script
    # effect that ends a naval battle without moving ships or damaging them;
    # org-only damage is the only automatic way to make a task force disengage.
    # Apply it only to sides that actually left, so a side still fighting keeps
    # its organization.
    player_breakoff = player_all_retreated and retreat_action == "org"
    enemy_breakoff = enemy_all_retreated and retreat_action == "org"
    if player_breakoff:
        p_org, p_str, p_org_den, p_str_den = 1.0, 0.0, 1.0, 1.0
    if enemy_breakoff:
        e_org, e_str, e_org_den, e_str_den = 1.0, 0.0, 1.0, 1.0

    plan = WritebackPlan(
        player_tag=context.player_tag,
        enemy_tag=context.enemy_tag,
        province=context.province,
        player_org_damage=min(1.0, p_org / p_org_den),
        player_str_damage=min(1.0, p_str / p_str_den),
        enemy_org_damage=min(1.0, e_org / e_org_den),
        enemy_str_damage=min(1.0, e_str / e_str_den),
        player_sunk=p_sunk,
        enemy_sunk=e_sunk,
        player_survivors=p_surv,
        enemy_survivors=e_surv,
        all_retreated=all_retreated,
        player_all_retreated=player_all_retreated,
        enemy_all_retreated=enemy_all_retreated,
        player_lost_names=p_lost,
        enemy_lost_names=e_lost,
        warnings=p_warn + e_warn,
    )

    def pct(value: float) -> str:
        return f"{round(value * 100)}%"

    parts = [
        f"{context.zone_name}（{context.naval_terrain}）",
        f"我方剩余 {p_surv} 艘，损失 {sum(p_sunk.values())} 艘，撤离 {len(result.player_retreated)} 艘",
        f"敌方剩余 {e_surv} 艘，损失 {sum(e_sunk.values())} 艘，撤离 {len(result.enemy_retreated)} 艘",
        f"我方舰队组织度损失 {pct(plan.player_org_damage)}、结构值损失 {pct(plan.player_str_damage)}",
    ]
    if all_retreated:
        if player_breakoff and enemy_breakoff:
            parts.insert(
                0,
                "双方全员主动脱离接触，未损失舰船；为结束原版海战，双方组织度暂时清零（会自然恢复），结构值无损",
            )
        else:
            parts.insert(
                0,
                "双方全员主动脱离接触，未造成组织度或结构值损失；原版海战可能需要手动撤离",
            )
    elif player_all_retreated:
        if player_breakoff:
            parts.insert(
                0,
                "我方全员主动撤离，我方未损失舰船且结构值无损；为结束原版海战，我方组织度暂时清零（会自然恢复）；敌方仍留场，其战损按结果写回",
            )
        else:
            parts.insert(
                0,
                "我方全员主动撤离，我方未造成组织度或结构值损失；敌方仍留场，其战损按结果写回；原版海战可能需要手动撤离",
            )
    elif enemy_all_retreated:
        if enemy_breakoff:
            parts.insert(
                0,
                "敌方全员主动撤离，敌方未损失舰船且结构值无损；为结束原版海战，敌方组织度暂时清零（会自然恢复）；我方仍留场，其战损按结果写回",
            )
        else:
            parts.insert(
                0,
                "敌方全员主动撤离，敌方未造成组织度或结构值损失；我方仍留场，其战损按结果写回；原版海战可能需要手动撤离",
            )
    plan.summary = "；".join(parts) + "。"
    return plan


def event_variables(result: BattleResult, plan: WritebackPlan) -> Dict[str, str]:
    outcome = result.outcome_cn() if result else "未分胜负"
    summary = plan.summary if plan else "战斗结束。"
    # Keep the console-injected strings short; loc adds the framing text.
    return {
        "ntc_outcome": outcome,
        "ntc_summary": summary[:600],
    }


def dump_context(context: BattleContext, path: str) -> None:
    payload = {
        "player_tag": context.player_tag,
        "enemy_tag": context.enemy_tag,
        "province": context.province,
        "zone_name": context.zone_name,
        "naval_terrain": context.naval_terrain,
        "preset": context.preset,
        "seed": context.seed,
        "player_doctrine": context.player_doctrine,
        "enemy_doctrine": context.enemy_doctrine,
        "player_navy_techs": context.player_navy_techs,
        "enemy_navy_techs": context.enemy_navy_techs,
        "player_ships": [
            {
                "id": s.ship_id,
                "name": s.name,
                "display_name": s.display_name,
                "type": s.definition,
                "navy_type": s.navy_type,
                "strength": s.start_strength,
                "max_strength": s.max_strength,
                "org": s.start_org,
                "max_org": s.max_org,
                "attributes": s.attributes,
            }
            for s in context.player_ships
        ],
        "player_aircraft": context.player_aircraft,
        "enemy_ships": [
            {
                "id": s.ship_id,
                "name": s.name,
                "display_name": s.display_name,
                "type": s.definition,
                "navy_type": s.navy_type,
                "strength": s.start_strength,
                "max_strength": s.max_strength,
                "org": s.start_org,
                "max_org": s.max_org,
                "attributes": s.attributes,
            }
            for s in context.enemy_ships
        ],
        "enemy_aircraft": context.enemy_aircraft,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
