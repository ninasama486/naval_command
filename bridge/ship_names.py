"""Resolve generated HOI4 ship names.

When a ship has no explicit `override`, the save stores:
    ship_name = { type = 1 name_order = 5 is_name_ordered = no }
and the hull equipment variant names a group, e.g.
    division_names_group = "ITA_BB_HISTORICAL"

The group's ordered name list lives in
`common/units/names_ships/<TAG>_ship_names.txt`. Combining the two recovers the
real ship name (e.g. "RN Caio Duilio"). This is what makes the tactical battle
show the same names the player sees in HOI4.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from hoi4_save import SaveData, _block_end


@dataclass
class NameGroup:
    prefix: str = ""
    fallback: str = ""
    names: List[str] = field(default_factory=list)


def _parse_group_block(block: str) -> NameGroup:
    prefix_m = re.search(r'(?m)^\s*prefix\s*=\s*"([^"]*)"', block)
    fallback_m = re.search(r'(?m)^\s*fallback_name\s*=\s*"([^"]*)"', block)
    names: List[str] = []
    for key in ("unique", "names", "name_list"):
        m = re.search(r"(?m)^\s*" + key + r"\s*=\s*\{", block)
        if not m:
            continue
        brace = block.find("{", m.start())
        end = _block_end(block, brace)
        if end < 0:
            continue
        body = block[brace + 1:end - 1]
        names = re.findall(r'"([^"]*)"', body)
        if names:
            break
    return NameGroup(
        prefix=prefix_m.group(1) if prefix_m else "",
        fallback=fallback_m.group(1) if fallback_m else "",
        names=names,
    )


def load_name_groups(game_dir: str) -> Dict[str, NameGroup]:
    groups: Dict[str, NameGroup] = {}
    pattern = os.path.join(game_dir, "common", "units", "names_ships", "*.txt")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in re.finditer(r"(?m)^([A-Za-z0-9_]+)\s*=\s*\{", text):
            key = m.group(1)
            brace = text.find("{", m.start(), m.end() + 1)
            end = _block_end(text, brace)
            if end < 0:
                continue
            # Only group blocks have a ship_types/unique/prefix section.
            block = text[m.start():end]
            if "ship_types" not in block and "unique" not in block:
                continue
            groups[key] = _parse_group_block(block)
    return groups


def resolve_ship_names(save: SaveData, groups: Dict[str, NameGroup]) -> int:
    """Fill Ship.name from override/name-order. Returns how many were resolved."""
    resolved = 0
    for ship in save.ships:
        if ship.name_override:
            ship.name = ship.name_override
            resolved += 1
            continue
        group_key = save.variant_name_group(ship)
        group = groups.get(group_key)
        if group is not None and 0 <= ship.name_order < len(group.names):
            base = group.names[ship.name_order]
            ship.name = f"{group.prefix}{base}" if group.prefix else base
            ship.name_group = group_key
            ship.name_prefix = group.prefix
            resolved += 1
        else:
            ship.name = f"{ship.definition} #{ship.ship_id}"
            ship.name_group = group_key
    return resolved
