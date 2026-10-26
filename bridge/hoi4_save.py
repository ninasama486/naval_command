"""Targeted parser for the parts of a HOI4 text save the bridge needs.

We intentionally do not build a full Clausewitz AST: a 70 MB save would balloon
into hundreds of MB of Python objects. Instead we locate the few blocks we need
(fleets -> task forces -> ships, country technologies, doctrines, wars) with
brace matching and pull scalar fields with regexes.

The save format is stable for 1.19.x. Anything we cannot find degrades to a
documented default instead of aborting the whole scan.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from models import AirWing, Fleet, Ship, TaskForce


# ---------------------------------------------------------------------------
# Low-level block scanning
# ---------------------------------------------------------------------------

def _block_end(text: str, open_index: int, limit: Optional[int] = None) -> int:
    """Return the index just past the matching '}' for the '{' at open_index."""
    if open_index < 0 or open_index >= len(text) or text[open_index] != "{":
        return -1
    depth = 0
    in_string = False
    escaped = False
    end_limit = len(text) if limit is None else min(limit, len(text))
    i = open_index
    while i < end_limit:
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return -1


def iter_named_blocks(text: str, key: str) -> Iterator[Tuple[int, int]]:
    """Yield (start, end) spans of every `key = { ... }` block."""
    pattern = re.compile(r"(?m)^[ \t]*" + re.escape(key) + r"\s*=\s*\{")
    for m in pattern.finditer(text):
        brace = text.find("{", m.start(), m.end() + 1)
        end = _block_end(text, brace)
        if end > 0:
            yield (m.start(), end)


def _largest_named_block(text: str, key: str) -> Optional[Tuple[int, int]]:
    best = None
    best_size = -1
    for start, end in iter_named_blocks(text, key):
        size = end - start
        if size > best_size:
            best = (start, end)
            best_size = size
    return best


def _field_str(block: str, name: str, default: str = "") -> str:
    m = re.search(r'(?m)^[ \t]*' + re.escape(name) + r'\s*=\s*"([^"]*)"', block)
    return m.group(1) if m else default


def _field_num(block: str, name: str, default: float = 0.0) -> float:
    m = re.search(
        r"(?m)^[ \t]*" + re.escape(name) + r"\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)", block
    )
    if not m:
        return default
    try:
        return float(m.group(1))
    except ValueError:
        return default


def _first_token(block: str, name: str, default: str = "") -> str:
    m = re.search(r"(?m)^[ \t]*" + re.escape(name) + r"\s*=\s*([^\s{}]+)", block)
    return m.group(1).strip('"') if m else default


# ---------------------------------------------------------------------------
# Data model of the parsed save
# ---------------------------------------------------------------------------

@dataclass
class SaveData:
    path: str = ""
    date: str = "1936.1.1.0"
    player_tag: str = ""
    fleets: List[Fleet] = field(default_factory=list)
    task_forces: List[TaskForce] = field(default_factory=list)
    ships: List[Ship] = field(default_factory=list)
    air_wings: List[AirWing] = field(default_factory=list)
    war_pairs: Set[frozenset] = field(default_factory=set)
    max_org_by_def: Dict[str, float] = field(default_factory=dict)
    max_org_by_tag_def: Dict[Tuple[str, str], float] = field(default_factory=dict)
    max_strength_by_variant: Dict[int, float] = field(default_factory=dict)
    techs: Set[str] = field(default_factory=set)
    grand_doctrine: str = ""
    naval_combat_provinces: Set[int] = field(default_factory=set)
    variant_index: Dict[int, Tuple[str, int, int]] = field(default_factory=dict)
    techs_by_tag: Dict[str, Set[str]] = field(default_factory=dict)
    doctrine_by_tag: Dict[str, str] = field(default_factory=dict)
    _text: str = ""
    _variant_modules_cache: Dict[int, Dict[str, str]] = field(default_factory=dict)
    _variant_upgrades_cache: Dict[int, List[Tuple[str, int]]] = field(default_factory=dict)
    _variant_bonus_cache: Dict[int, Dict[str, float]] = field(default_factory=dict)
    _weather_block: Optional[Tuple[int, int]] = None

    # -- helpers ----------------------------------------------------------
    def at_war(self, a: str, b: str) -> bool:
        if not a or not b or a == b:
            return False
        return frozenset((a, b)) in self.war_pairs

    def ships_at(self, province: int, tag: Optional[str] = None) -> List[Ship]:
        out = [s for s in self.ships if s.location == province]
        if tag is not None:
            out = [s for s in out if s.tag == tag]
        return out

    def task_forces_at(self, province: int, tag: Optional[str] = None) -> List[TaskForce]:
        out = [tf for tf in self.task_forces if tf.location == province]
        if tag is not None:
            out = [tf for tf in out if tf.tag == tag]
        return out

    def max_org(self, definition: str) -> float:
        return self.max_org_by_def.get(definition, 40.0)

    def max_org_for(self, ship: Ship) -> float:
        """Per-country maximum org is more accurate than a global maximum:
        naval doctrines and techs change the pool between countries."""
        observed = self.max_org_by_tag_def.get((ship.tag, ship.definition), 0.0)
        if observed > 0.0:
            return max(observed, CLASS_ORG_FLOOR.get(ship.definition, 40.0))
        return self.max_org(ship.definition)

    def max_strength(self, ship: Ship) -> float:
        """Best estimate of the ship's full structural value.

        The save stores absolute strength points, not a percentage. The
        maximum observed strength for the same equipment variant across the
        save is a reliable estimate: navies start with full-strength ships, so
        at least one hull of a variant is usually undamaged. Falls back to the
        ship's own current strength.
        """
        observed = self.max_strength_by_variant.get(ship.equipment_id, 0.0)
        return max(ship.strength, observed, 1.0)

    def variant_block_by_id(self, equipment_id: int) -> str:
        span = self.variant_index.get(equipment_id)
        if span is None:
            return ""
        _key, start, end = span
        return self._text[start:end]

    def variant_key_by_id(self, equipment_id: int) -> str:
        span = self.variant_index.get(equipment_id)
        return span[0] if span else ""

    def modules_by_id(self, equipment_id: int) -> Dict[str, str]:
        cached = self._variant_modules_cache.get(equipment_id)
        if cached is not None:
            return cached
        block = self.variant_block_by_id(equipment_id)
        modules: Dict[str, str] = {}
        if block:
            m = re.search(r"modules\s*=\s*\{([\s\S]*?)\}", block)
            if m:
                for sm in re.finditer(r"([A-Za-z0-9_]+)\s*=\s*([A-Za-z0-9_]+)", m.group(1)):
                    slot, token = sm.group(1), sm.group(2)
                    if token and token != "empty":
                        modules[slot] = token
        self._variant_modules_cache[equipment_id] = modules
        return modules

    def modules_for_ship(self, ship: Ship) -> Dict[str, str]:
        return self.modules_by_id(ship.equipment_id)

    def variant_key(self, ship: Ship) -> str:
        return self.variant_key_by_id(ship.equipment_id)

    def upgrades_by_id(self, equipment_id: int) -> List[Tuple[str, int]]:
        cached = self._variant_upgrades_cache.get(equipment_id)
        if cached is not None:
            return cached
        block = self.variant_block_by_id(equipment_id)
        upgrades: List[Tuple[str, int]] = []
        if block:
            m = re.search(r"(?ms)^[ \t]*upgrades\s*=\s*\{\s*upgrades\s*=\s*\{", block)
            if m:
                brace = block.find("{", m.start(), m.end() + 1)
                # m matches the outer upgrades block; find its end.
                outer_end = _block_end(block, brace)
                if outer_end > 0:
                    body = block[m.end():outer_end]
                    for um in re.finditer(r"\{\s*([A-Za-z0-9_]+)\s+(\d+)\s*\}", body):
                        upgrades.append((um.group(1), int(um.group(2))))
        self._variant_upgrades_cache[equipment_id] = upgrades
        return upgrades

    def design_bonus_by_id(self, equipment_id: int) -> Dict[str, float]:
        cached = self._variant_bonus_cache.get(equipment_id)
        if cached is not None:
            return cached
        block = self.variant_block_by_id(equipment_id)
        bonus: Dict[str, float] = {}
        if block:
            m = re.search(r"(?ms)^[ \t]*design_team_bonus\s*=\s*\{", block)
            if m:
                brace = block.find("{", m.start(), m.end() + 1)
                end = _block_end(block, brace)
                if end > 0:
                    for km in re.finditer(
                        r"([A-Za-z0-9_]+)\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)",
                        block[brace:end],
                    ):
                        bonus[km.group(1)] = float(km.group(2))
        self._variant_bonus_cache[equipment_id] = bonus
        return bonus

    def air_wings_for(self, carrier_ship_id: int) -> List[AirWing]:
        return [w for w in self.air_wings if w.carrier_ship_id == carrier_ship_id]

    def variant_name_group(self, ship: Ship) -> str:
        span = self.variant_index.get(ship.equipment_id)
        if span is None:
            return ""
        _key, start, end = span
        m = re.search(
            r'division_names_group\s*=\s*"([^"]+)"',
            self._text[start:min(end, start + 4000)],
        )
        return m.group(1) if m else ""

    def techs_for(self, tag: str) -> Set[str]:
        if tag not in self.techs_by_tag:
            self.techs_by_tag[tag] = _parse_techs(self._text, tag) if self._text else set()
        return self.techs_by_tag[tag]

    def doctrine_for(self, tag: str) -> str:
        if tag not in self.doctrine_by_tag:
            self.doctrine_by_tag[tag] = (
                _parse_grand_doctrine(self._text, tag) if self._text else ""
            )
        return self.doctrine_by_tag[tag]

    def weather_for(self, province: int) -> str:
        """Best-effort HOI4 weather -> navy-out weather token.

        The save's weather block stores precipitation/water and snow per
        province. Snow is treated as light rain; heavy water as heavy rain.
        """
        if not self._text:
            return "clear"
        if self._weather_block is None:
            self._weather_block = _largest_named_block(self._text, "weather") or (-1, -1)
        start, end = self._weather_block
        if start < 0:
            return "clear"
        block = self._text[start:end]
        m = re.search(r'"' + str(province) + r'"\s*=\s*\{([\s\S]*?)\}', block)
        if not m:
            return "clear"
        body = m.group(1)
        water = _field_num(body, "water", 0.0)
        snow = _field_num(body, "snow", 0.0)
        if water >= 3.0:
            return "heavy_rain"
        if water >= 0.3 or snow > 0.0:
            return "light_rain"
        return "clear"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

CLASS_ORG_FLOOR = {
    "destroyer": 35.0,
    "light_cruiser": 40.0,
    "heavy_cruiser": 40.0,
    "battle_cruiser": 50.0,
    "battleship": 65.0,
    "carrier": 60.0,
    "submarine": 40.0,
}


def _parse_ship(block: str, tag: str, tf: TaskForce) -> Optional[Ship]:
    id_m = re.search(r"id=\{\s*id=(\d+)\s+type=51\s*\}", block)
    if not id_m:
        return None
    definition = _first_token(block, "definition")
    if not definition:
        return None
    equipment_m = re.search(
        r"equipment\s*=\s*\{\s*equipment\s*=\s*\{\s*id=\{\s*id=(\d+)\s+type=70", block
    )
    equipment_id = int(equipment_m.group(1)) if equipment_m else 0
    name_override = ""
    name_order = -1
    nm = re.search(r'ship_name\s*=\s*\{([\s\S]*?)\}', block)
    if nm:
        body = nm.group(1)
        om = re.search(r'override\s*=\s*"([^"]*)"', body)
        if om:
            name_override = om.group(1)
        nom = re.search(r"name_order\s*=\s*(\d+)", body)
        if nom:
            name_order = int(nom.group(1))
    name = name_override or f"{definition} #{id_m.group(1)}"
    return Ship(
        ship_id=int(id_m.group(1)),
        definition=definition,
        organisation=max(0.0, _field_num(block, "organisation", 0.0)),
        strength=max(0.0, _field_num(block, "strength", 100.0)),
        name=name,
        equipment_id=equipment_id,
        tag=tag,
        task_force_id=tf.task_force_id,
        task_force_name=tf.name,
        location=tf.location,
        name_override=name_override,
        name_order=name_order,
    )


def _parse_task_force(block: str, fleet_name: str) -> Optional[TaskForce]:
    tag = _field_str(block, "logical_country")
    if not tag:
        return None
    id_m = re.search(r"id=\{\s*id=(\d+)\s+type=51\s*\}", block)
    tf_id = int(id_m.group(1)) if id_m else 0
    tf = TaskForce(
        task_force_id=tf_id,
        name=_field_str(block, "name", f"task force #{tf_id}"),
        location=int(_field_num(block, "location", 0)),
        tag=tag,
        last_combat_date=_field_str(block, "last_combat_date", "1.1.1.1"),
        fleet_name=fleet_name,
    )
    for start, end in iter_named_blocks(block, "ship"):
        ship = _parse_ship(block[start:end], tag, tf)
        if ship is not None:
            tf.ships.append(ship)
    return tf


def _parse_fleets(text: str, save: SaveData) -> None:
    for fstart, fend in iter_named_blocks(text, "fleet"):
        fblock = text[fstart:fend]
        fleet_name = _field_str(fblock, "name", "fleet")
        fleet = Fleet(name=fleet_name)
        for tstart, tend in iter_named_blocks(fblock, "task_force"):
            tf = _parse_task_force(fblock[tstart:tend], fleet_name)
            if tf is None:
                continue
            fleet.task_forces.append(tf)
            save.task_forces.append(tf)
            save.ships.extend(tf.ships)
        if fleet.task_forces:
            save.fleets.append(fleet)
            # Location / tag on the ship is the task force's; fill now.
            for tf in fleet.task_forces:
                for ship in tf.ships:
                    ship.location = tf.location
                    ship.tag = tf.tag


def _parse_air_wings(text: str, save: SaveData) -> None:
    """Map carrier air bases to carriers and extract one entry per air wing.

    Save structure (1.19.x):
      air_base={ id={ id=332 type=65 } carrier={ id=112 type=51 } ... }
      air_wing_pool={
          definition=cv_fighter
          air_base={ id=332 type=65 }
          air_wings={ id={...} count=9 tag="ENG" equipment={...} name="..." }
      }
    """
    carrier_bases: Dict[int, int] = {}
    for start, end in iter_named_blocks(text, "air_base"):
        block = text[start:end]
        bm = re.search(r"id=\{\s*id=(\d+)\s+type=65\s*\}", block)
        cm = re.search(r"carrier=\{\s*id=(\d+)\s+type=51\s*\}", block)
        if bm and cm:
            carrier_bases[int(bm.group(1))] = int(cm.group(1))

    for start, end in iter_named_blocks(text, "air_wing_pool"):
        pool = text[start:end]
        dm = re.search(r"(?m)^[ \t]*definition=([a-z_]+)", pool)
        bm = re.search(r"air_base=\{\s*id=(\d+)\s+type=65\s*\}", pool)
        if not dm or not bm:
            continue
        carrier_id = carrier_bases.get(int(bm.group(1)))
        if not carrier_id:
            continue
        definition = dm.group(1)
        for ws, we in iter_named_blocks(pool, "air_wings"):
            wing = pool[ws:we]
            wm = re.search(r"id=\{\s*id=(\d+)\s+type=69\s*\}", wing)
            cm = re.search(r"(?m)^[ \t]*count=(\d+)", wing)
            if not wm or not cm:
                continue
            count = int(cm.group(1))
            if count <= 0:
                continue
            equipment = re.findall(
                r"equipment=\{\s*id=\{\s*id=(\d+)\s+type=70\s*\}\s*amount=(\d+)",
                wing,
            )
            if not equipment:
                continue
            eq_id, _amount = max(equipment, key=lambda pair: int(pair[1]))
            tm = re.search(r'tag="([A-Z]{3})"', wing)
            nm = re.search(r'(?m)^[ \t]*name="([^"]*)"', wing)
            save.air_wings.append(
                AirWing(
                    wing_id=int(wm.group(1)),
                    carrier_ship_id=carrier_id,
                    definition=definition,
                    count=count,
                    equipment_id=int(eq_id),
                    tag=tm.group(1) if tm else "",
                    name=nm.group(1) if nm else "",
                )
            )


def _parse_wars(text: str) -> Set[frozenset]:
    pairs: Set[frozenset] = set()
    for m in re.finditer(
        r'war_relation\s*=\s*\{\s*first\s*=\s*"([A-Z]{3})"\s*second\s*=\s*"([A-Z]{3})"',
        text,
    ):
        a, b = m.group(1), m.group(2)
        if a != b:
            pairs.add(frozenset((a, b)))
    return pairs


def _country_block(text: str, tag: str) -> Optional[str]:
    blocks = [(s, e) for s, e in iter_named_blocks(text, tag)]
    if not blocks:
        return None
    # A tag can appear in player_countries and air-wing bookkeeping as well as
    # the real country state. The real block is the one carrying technology /
    # fleet data; fall back to the largest block for odd saves.
    with_tech = [(s, e) for s, e in blocks if "technology={" in text[s:e]]
    pool = with_tech or blocks
    s, e = max(pool, key=lambda se: se[1] - se[0])
    return text[s:e]


def _parse_techs(text: str, tag: str) -> Set[str]:
    block = _country_block(text, tag)
    if not block:
        return set()
    tech_root = _largest_named_block(block, "technology")
    if not tech_root:
        return set()
    tech_block = block[tech_root[0]:tech_root[1]]
    techs_block = _largest_named_block(tech_block, "technologies")
    if not techs_block:
        return set()
    body = tech_block[techs_block[0]:techs_block[1]]
    # Direct children are indented by four tabs in the 1.19 save; accept any
    # indent >= 2 tabs so small mods with different serialization still work.
    keys: Set[str] = set()
    depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if depth == 1:
            m = re.match(r"([A-Za-z0-9_]+)\s*=\s*\{", stripped)
            if m:
                keys.add(m.group(1))
        depth += stripped.count("{") - stripped.count("}")
    return keys


def _parse_grand_doctrine(text: str, tag: str) -> str:
    for m in re.finditer(
        r'grand_doctrine\s*=\s*"([^"]+)"\s*\n\s*folder\s*=\s*"naval"', text
    ):
        window = text[m.end():m.end() + 30000]
        cm = re.search(r'country\s*=\s*"([A-Z]{3})"', window)
        if cm and cm.group(1) == tag:
            return m.group(1)
    return ""


def _parse_variant_index(text: str) -> Dict[int, Tuple[str, int, int]]:
    eq = _largest_named_block(text, "equipments")
    if not eq:
        return {}
    start, end = eq
    body = text[start:end]
    index: Dict[int, Tuple[str, int, int]] = {}
    pattern = re.compile(r"(?m)^\t([A-Za-z0-9_]+)\s*=\s*\{\s*\n\t\tid=\{\s*id=(\d+)\s+type=70")
    for m in pattern.finditer(body):
        brace = body.find("{", m.start(), m.end() + 1)
        bend = _block_end(body, brace)
        if bend < 0:
            continue
        index[int(m.group(2))] = (m.group(1), start + m.start(), start + bend)
    return index


def _parse_naval_combat_provinces(text: str) -> Set[int]:
    provinces: Set[int] = set()
    for key in ("naval_combat", "naval_battle", "sea_battle"):
        for start, end in iter_named_blocks(text, key):
            block = text[start:end]
            loc = re.search(r"(?m)^[ \t]*(?:location|province)\s*=\s*(\d+)", block)
            if loc:
                provinces.add(int(loc.group(1)))
    return provinces


def _fill_max_org(save: SaveData) -> None:
    observed: Dict[str, float] = {}
    observed_tag: Dict[Tuple[str, str], float] = {}
    for ship in save.ships:
        observed[ship.definition] = max(observed.get(ship.definition, 0.0), ship.organisation)
        key = (ship.tag, ship.definition)
        observed_tag[key] = max(observed_tag.get(key, 0.0), ship.organisation)
    for definition, value in observed.items():
        floor = CLASS_ORG_FLOOR.get(definition, 40.0)
        save.max_org_by_def[definition] = max(value, floor)
    for definition, floor in CLASS_ORG_FLOOR.items():
        save.max_org_by_def.setdefault(definition, floor)
    save.max_org_by_tag_def = observed_tag
    for ship in save.ships:
        ship.max_org = save.max_org_for(ship)


def _fill_war_pairs_fallback(save: SaveData) -> None:
    """If the save has no war_relation blocks (modded/odd saves), treat every
    non-player tag as a potential enemy. Candidates are still filtered by both
    fleets sharing a sea province, so the false-positive rate is low."""
    if save.war_pairs or not save.player_tag:
        return
    tags = {tf.tag for tf in save.task_forces if tf.tag}
    for tag in tags:
        if tag != save.player_tag:
            save.war_pairs.add(frozenset((save.player_tag, tag)))


def load_save(path: str, *, keep_text: bool = True) -> SaveData:
    """Parse the naval-relevant parts of a HOI4 text save.

    `keep_text` must stay True for the lazy loaders (equipment modules,
    country technologies, weather); the full text is ~70 MB, which is fine for
    a desktop bridge.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    save = SaveData(path=path, _text=text if keep_text else "")
    save.date = _field_str(text[:200000], "date", "1936.1.1.0")
    save.player_tag = _field_str(text[:200000], "player", "")

    save.war_pairs = _parse_wars(text)
    _parse_fleets(text, save)
    _parse_air_wings(text, save)
    save.max_org_by_def = {}
    _fill_max_org(save)
    by_variant: Dict[int, float] = {}
    for ship in save.ships:
        by_variant[ship.equipment_id] = max(
            by_variant.get(ship.equipment_id, 0.0), ship.strength
        )
    save.max_strength_by_variant = by_variant
    for ship in save.ships:
        ship.max_strength = save.max_strength(ship)
    _fill_war_pairs_fallback(save)

    if save.player_tag:
        save.techs = _parse_techs(text, save.player_tag)
        save.grand_doctrine = _parse_grand_doctrine(text, save.player_tag)
        save.techs_by_tag[save.player_tag] = save.techs
        save.doctrine_by_tag[save.player_tag] = save.grand_doctrine

    save.variant_index = _parse_variant_index(text)
    save.naval_combat_provinces = _parse_naval_combat_provinces(text)

    if not keep_text:
        save._text = ""
        save.variant_index = {}
        save.techs_by_tag.clear()
        save.doctrine_by_tag.clear()

    return save


def save_summary(save: SaveData) -> str:
    return (
        f"date={save.date} player={save.player_tag} fleets={len(save.fleets)} "
        f"task_forces={len(save.task_forces)} ships={len(save.ships)} "
        f"wars={len(save.war_pairs)} naval_combat_zones={len(save.naval_combat_provinces)} "
        f"doctrine={save.grand_doctrine or '-'}"
    )
