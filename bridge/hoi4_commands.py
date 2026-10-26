"""Console command builders for the HOI4 side."""

from __future__ import annotations

from typing import Dict, List

from collections import defaultdict

from models import BattleCandidate, WritebackPlan


# `destroy_ships` takes a ship *hull archetype*, not the save's `definition`.
# Vanilla example: FRA scuttling uses ship_hull_light / ship_hull_cruiser /
# ship_hull_heavy / ship_hull_carrier / ship_hull_submarine. Passing
# `light_cruiser` etc. silently destroys nothing.
DESTROY_HULL_TYPE = {
    "destroyer": "ship_hull_light",
    "light_cruiser": "ship_hull_cruiser",
    "heavy_cruiser": "ship_hull_cruiser",
    "battle_cruiser": "ship_hull_heavy",
    "battlecruiser": "ship_hull_heavy",
    "battleship": "ship_hull_heavy",
    "carrier": "ship_hull_carrier",
    "submarine": "ship_hull_submarine",
}


def hoi4_string(value: str) -> str:
    """Escape a string for a HOI4 effect argument.

    Square brackets are replaced with full-width brackets: they would
    otherwise be parsed as localisation commands when the variable is
    displayed in the result event.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("[", "（").replace("]", "）")
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    if len(text) > 600:
        text = text[:597] + "..."
    return text


def set_vars_command(tag: str, variables: Dict[str, str]) -> str:
    parts = []
    for key, value in variables.items():
        parts.append(f'set_variable = {{ {key} = "{hoi4_string(value)}" }}')
    return f"eval_effect {tag} = {{ " + " ".join(parts) + " }"


def scan_event_commands(tag: str, battles: List[BattleCandidate]) -> List[str]:
    """Fire ntc_scan.1 with ntc_b1..3 set to the battle summaries."""
    values = {f"ntc_b{i}": "（无）" for i in (1, 2, 3)}
    for idx, battle in enumerate(battles[:3], start=1):
        values[f"ntc_b{idx}"] = battle.summary or battle.title()
    return [
        "gamespeed 0",
        set_vars_command(tag, values),
        f"event ntc_scan.1 {tag}",
    ]


def mode_on_commands(tag: str) -> List[str]:
    return [
        "gamespeed 0",
        f"eval_effect {tag} = {{ set_country_flag = ntc_mode_active clr_country_flag = ntc_scan_waiting }}",
    ]


def abort_commands(tag: str) -> List[str]:
    return [
        f"eval_effect {tag} = {{ country_event = {{ id = ntc_quiet.1 hours = 0 }} }}",
    ]


def writeback_commands(plan: WritebackPlan) -> List[str]:
    commands: List[str] = ["gamespeed 0"]

    for tag, sunk in ((plan.player_tag, plan.player_sunk), (plan.enemy_tag, plan.enemy_sunk)):
        grouped: Dict[str, int] = defaultdict(int)
        for ship_type, count in sunk.items():
            if count <= 0 or not ship_type:
                continue
            hull = DESTROY_HULL_TYPE.get(ship_type)
            if hull is None:
                # Already a hull key (future-proof), otherwise skip rather than
                # emit a command the engine ignores.
                hull = ship_type if ship_type.startswith("ship_hull_") else ""
            if hull:
                grouped[hull] += int(count)
        for hull, count in sorted(grouped.items()):
            commands.append(
                f"eval_effect {tag} = {{ destroy_ships = {{ type = {hull} count = {count} }} }}"
            )

    for tag, org, strdmg in (
        (plan.player_tag, plan.player_org_damage, plan.player_str_damage),
        (plan.enemy_tag, plan.enemy_org_damage, plan.enemy_str_damage),
    ):
        org = max(0.0, min(1.0, org))
        strdmg = max(0.0, min(1.0, strdmg))
        if org < 0.0005 and strdmg < 0.0005:
            continue
        fields = []
        if org >= 0.0005:
            fields.append(f"org_damage = {org:.3f}")
        if strdmg >= 0.0005:
            fields.append(f"str_damage = {strdmg:.3f}")
        commands.append(
            "eval_effect damage_units = { province = %d limit = { tag = %s } %s ratio = yes navy = yes }"
            % (plan.province, tag, " ".join(fields))
        )

    return commands


def result_event_commands(tag: str, outcome: str, summary: str) -> List[str]:
    return [
        set_vars_command(tag, {"ntc_outcome": outcome, "ntc_summary": summary}),
        f"eval_effect {tag} = {{ country_event = {{ id = ntc_end.1 hours = 0 }} }}",
    ]
