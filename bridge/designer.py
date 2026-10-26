"""HOI4 equipment designer calculations for ships and carrier aircraft.

This module reads the same data the game designers use:

  * ship hull base stats           common/units/equipment/ship_hull_*.txt
  * ship modules                   common/units/equipment/modules/00_ship_modules.txt
  * ship upgrades                  common/units/equipment/upgrades/naval_upgrades.txt
  * plane airframes                common/units/equipment/plane_airframes.txt
                                   common/units/equipment/*_engine_airframe.txt
                                   common/units/equipment/x_plane_airframes.txt
  * plane modules                  common/units/equipment/modules/00_plane_modules.txt
  * plane upgrades                 common/units/equipment/upgrades/air_upgrades.txt

Aggregation follows the Clausewitz designer rules:

  add_stats          -> flat sum
  multiply_stats     -> additive percentage modifiers (0.05 = +5%)
  add_average_stats  -> highest value + average of the remaining values
                        (HOI4's multi-module "average" rule)
  upgrades           -> per-level percentage modifiers (carrier_size is flat)
  design_team_bonus  -> percentage modifiers

The resulting values are the ship/plane equipment stats, not navy-out's class
baseline. They are passed to navy-out as external attribute overrides.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Generic Clausewitz block helpers
# ---------------------------------------------------------------------------

def _block_end(text: str, open_index: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for i in range(open_index, len(text)):
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
    return -1


def _iter_blocks(text: str, key: str) -> Iterable[Tuple[int, int]]:
    pattern = re.compile(r"(?m)^[ \t]*" + re.escape(key) + r"\s*=\s*\{")
    for m in pattern.finditer(text):
        brace = text.find("{", m.start(), m.end() + 1)
        end = _block_end(text, brace)
        if end > 0:
            yield (m.start(), end)


def _largest_block(text: str, key: str) -> Optional[Tuple[int, int]]:
    best = None
    best_size = -1
    for start, end in _iter_blocks(text, key):
        if end - start > best_size:
            best = (start, end)
            best_size = end - start
    return best


def _direct_children(text: str, start: int, end: int) -> Iterable[Tuple[str, int, int]]:
    """Yield (key, start, end) for immediate child blocks of a block."""
    body_start = text.find("{", start, end)
    if body_start < 0:
        return
    i = body_start + 1
    while i < end:
        m = re.search(r"([A-Za-z0-9_]+)\s*=\s*\{", text[i:end])
        if not m:
            break
        key = m.group(1)
        brace = text.find("{", i + m.start(), i + m.end())
        bend = _block_end(text, brace)
        if bend < 0 or bend > end:
            break
        yield (key, i + m.start(), bend)
        i = bend


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _scalars(block: str, start: int = 0, end: Optional[int] = None) -> Dict[str, float]:
    """Read scalar key=value pairs at the immediate depth of a block.

    Nested blocks (module_slots, resources, ...) are skipped. Clausewitz
    comments are stripped first: several engine modules keep old
    `#naval_speed = 10` lines that must NOT become live stats.
    """
    if end is None:
        end = len(block)
    body = _strip_comments(block[start:end])
    depth = 0
    out: Dict[str, float] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if depth == 1:
            m = re.match(r"([A-Za-z0-9_]+)\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)\s*$", stripped)
            if m:
                out[m.group(1)] = float(m.group(2))
        depth += stripped.count("{") - stripped.count("}")
    return out


def _string_field(block: str, key: str) -> str:
    m = re.search(r"(?m)^[ \t]*" + re.escape(key) + r"\s*=\s*([A-Za-z0-9_]+)", block)
    return m.group(1) if m else ""


def _nested_scalars(block: str, key: str) -> Dict[str, float]:
    m = re.search(r"(?m)^[ \t]*" + re.escape(key) + r"\s*=\s*\{", block)
    if not m:
        return {}
    brace = block.find("{", m.start(), m.end() + 1)
    end = _block_end(block, brace)
    if end < 0:
        return {}
    body = _strip_comments(block[brace:end])
    out = {}
    for k, v in re.findall(r"([A-Za-z0-9_]+)\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)", body):
        out[k] = float(v)
    return out


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class EquipmentDef:
    key: str
    stats: Dict[str, float] = field(default_factory=dict)
    archetype: str = ""
    parent: str = ""
    block: str = ""


@dataclass
class ModuleDef:
    key: str
    add: Dict[str, float] = field(default_factory=dict)
    multiply: Dict[str, float] = field(default_factory=dict)
    average: Dict[str, float] = field(default_factory=dict)
    # mission_type_stats: (limit keywords, add, multiply, average)
    missions: List[Tuple[set, Dict[str, float], Dict[str, float], Dict[str, float]]] = field(
        default_factory=list
    )


@dataclass
class UpgradeDef:
    key: str
    per_level: Dict[str, float] = field(default_factory=dict)


@dataclass
class DuplicateArchetype:
    key: str
    archetype: str = ""
    type_name: str = ""
    mods: Dict[str, float] = field(default_factory=dict)
    mod_ops: Dict[str, str] = field(default_factory=dict)


def plane_mission_keys(role: str) -> set:
    """Mission keywords used by mission_type_stats for a plane role."""
    r = (role or "").lower()
    if "nav" in r and "bomber" in r or "torpedo" in r:
        return {"naval_bomber", "port_strike", "naval_patrol"}
    if "cas" in r or "close_air" in r:
        return {"close_air_support", "cas"}
    if "interceptor" in r or "fighter" in r:
        return {"air_superiority", "interception"}
    if "suicide" in r or "kamikaze" in r:
        return {"kamikaze"}
    if "scout" in r or "recon" in r:
        return {"recon", "naval_patrol"}
    return set()


# ---------------------------------------------------------------------------
# Designer database
# ---------------------------------------------------------------------------

class DesignerDB:
    def __init__(self, game_dir: str) -> None:
        self.game_dir = game_dir
        self.ship_hulls: Dict[str, EquipmentDef] = {}
        self.ship_modules: Dict[str, ModuleDef] = {}
        self.ship_upgrades: Dict[str, UpgradeDef] = {}
        self.plane_airframes: Dict[str, EquipmentDef] = {}
        self.plane_modules: Dict[str, ModuleDef] = {}
        self.plane_upgrades: Dict[str, UpgradeDef] = {}
        self.plane_duplicates: Dict[str, DuplicateArchetype] = {}
        self._ship_cache: Dict[str, Dict[str, float]] = {}
        self._plane_cache: Dict[str, Dict[str, float]] = {}
        self._load()

    # -- loading ---------------------------------------------------------
    def _read(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def _load_ship_hulls(self) -> None:
        paths = glob.glob(
            os.path.join(self.game_dir, "common", "units", "equipment", "ship_hull_*.txt")
        )
        for path in paths:
            text = self._read(path)
            eq = _largest_block(text, "equipments")
            if not eq:
                continue
            for key, start, end in _direct_children(text, *eq):
                block = text[start:end]
                self.ship_hulls[key] = EquipmentDef(
                    key=key,
                    stats=_scalars(block),
                    archetype=_string_field(block, "archetype"),
                    parent=_string_field(block, "parent"),
                    block=block,
                )

    def _load_modules(self, path: str) -> Dict[str, ModuleDef]:
        out: Dict[str, ModuleDef] = {}
        text = self._read(path)
        root = _largest_block(text, "equipment_modules")
        if not root:
            return out
        for key, start, end in _direct_children(text, *root):
            block = text[start:end]
            missions = []
            for m in re.finditer(r"(?m)^[ \t]*mission_type_stats\s*=\s*\{", block):
                brace = block.find("{", m.start(), m.end() + 1)
                mend = _block_end(block, brace)
                if mend < 0:
                    continue
                body = _strip_comments(block[m.end():mend])
                lm = re.search(r"limit\s*=\s*\{([^}]*)\}", body)
                keys = set()
                if lm:
                    keys = {
                        w for w in re.findall(r"[a-z_]+", lm.group(1))
                        if w not in ("limit", "and", "or", "not", "yes")
                    }
                missions.append(
                    (
                        keys,
                        _nested_scalars(body, "add_stats"),
                        _nested_scalars(body, "multiply_stats"),
                        _nested_scalars(body, "add_average_stats"),
                    )
                )
            out[key] = ModuleDef(
                key=key,
                add=_nested_scalars(block, "add_stats"),
                multiply=_nested_scalars(block, "multiply_stats"),
                average=_nested_scalars(block, "add_average_stats"),
                missions=missions,
            )
        return out

    def _load_upgrades(self, path: str) -> Dict[str, UpgradeDef]:
        out: Dict[str, UpgradeDef] = {}
        text = self._read(path)
        root = _largest_block(text, "upgrades")
        if not root:
            return out
        for key, start, end in _direct_children(text, *root):
            block = text[start:end]
            out[key] = UpgradeDef(key=key, per_level=_scalars(block))
        return out

    def _load_plane_airframes(self) -> None:
        files = [
            "plane_airframes.txt",
            "single_engine_airframe.txt",
            "twin_engine_airframe.txt",
            "quad_engine_airframe.txt",
            "x_plane_airframes.txt",
            "mothership.txt",
            "helicopter.txt",
        ]
        for name in files:
            path = os.path.join(self.game_dir, "common", "units", "equipment", name)
            text = self._read(path)
            if not text:
                continue
            eq = _largest_block(text, "equipments")
            if eq:
                for key, start, end in _direct_children(text, *eq):
                    block = text[start:end]
                    self.plane_airframes[key] = EquipmentDef(
                        key=key,
                        stats=_scalars(block),
                        archetype=_string_field(block, "archetype"),
                        parent=_string_field(block, "parent"),
                        block=block,
                    )
            # duplicate_archetypes can contain for_each stat overrides
            dup = _largest_block(text, "duplicate_archetypes")
            if dup:
                for key, start, end in _direct_children(text, *dup):
                    block = text[start:end]
                    mods: Dict[str, float] = {}
                    ops: Dict[str, str] = {}
                    fe = re.search(r"(?ms)^[ \t]*for_each\s*=\s*\{", block)
                    if fe:
                        brace = block.find("{", fe.start(), fe.end() + 1)
                        fend = _block_end(block, brace)
                        if fend > 0:
                            for sm in re.finditer(
                                r"([A-Za-z0-9_]+)\s*=\s*\{\s*(set|add|multiply)\s*=\s*(-?[0-9.]+)",
                                _strip_comments(block[brace:fend]),
                            ):
                                stat, op, val = sm.group(1), sm.group(2), float(sm.group(3))
                                mods[stat] = val
                                ops[stat] = op
                    self.plane_duplicates[key] = DuplicateArchetype(
                        key=key,
                        archetype=_string_field(block, "archetype"),
                        type_name=_string_field(block, "type"),
                        mods=mods,
                        mod_ops=ops,
                    )

    def _load(self) -> None:
        self._load_ship_hulls()
        self.ship_modules = self._load_modules(
            os.path.join(
                self.game_dir, "common", "units", "equipment", "modules", "00_ship_modules.txt"
            )
        )
        self.ship_upgrades = self._load_upgrades(
            os.path.join(
                self.game_dir, "common", "units", "equipment", "upgrades", "naval_upgrades.txt"
            )
        )
        self._load_plane_airframes()
        self.plane_modules = self._load_modules(
            os.path.join(
                self.game_dir, "common", "units", "equipment", "modules", "00_plane_modules.txt"
            )
        )
        self.plane_upgrades = self._load_upgrades(
            os.path.join(
                self.game_dir, "common", "units", "equipment", "upgrades", "air_upgrades.txt"
            )
        )

    # -- inheritance -----------------------------------------------------
    def _resolve_equipment(
        self, key: str, table: Dict[str, EquipmentDef], cache: Dict[str, Dict[str, float]],
        seen: Optional[set] = None,
    ) -> Dict[str, float]:
        if key in cache:
            return dict(cache[key])
        if seen is None:
            seen = set()
        if key in seen:
            return {}
        seen.add(key)
        item = table.get(key)
        if item is None:
            return {}
        merged: Dict[str, float] = {}
        for base in (item.archetype, item.parent):
            if base and base in table:
                merged.update(self._resolve_equipment(base, table, cache, seen))
        merged.update(item.stats)
        cache[key] = merged
        return dict(merged)

    def hull_stats(self, key: str) -> Dict[str, float]:
        return self._resolve_equipment(key, self.ship_hulls, self._ship_cache)

    def _plane_base_stats(self, key: str) -> Dict[str, float]:
        if key in self._plane_cache:
            return dict(self._plane_cache[key])
        # Exact legacy / modular equipment block.
        if key in self.plane_airframes:
            stats = self._resolve_equipment(key, self.plane_airframes, self._plane_cache)
            self._plane_cache[key] = stats
            return dict(stats)

        # Duplicate type + level, e.g. small_plane_naval_bomber_airframe_2
        m = re.match(r"(.+)_(\d+)$", key)
        if m:
            base_key, level = m.group(1), m.group(2)
            dup = self.plane_duplicates.get(base_key)
            if dup is not None:
                base_airframe = f"{dup.archetype}_{level}"
                stats = self._resolve_equipment(
                    base_airframe, self.plane_airframes, self._plane_cache
                )
                for stat, val in dup.mods.items():
                    op = dup.mod_ops.get(stat, "set")
                    if op == "set":
                        stats[stat] = val
                    elif op == "add":
                        stats[stat] = stats.get(stat, 0.0) + val
                    elif op == "multiply":
                        stats[stat] = stats.get(stat, 0.0) * val
                self._plane_cache[key] = stats
                return dict(stats)

        # Exact duplicate archetype key without a level.
        dup = self.plane_duplicates.get(key)
        if dup is not None:
            stats = self._resolve_equipment(
                dup.archetype, self.plane_airframes, self._plane_cache
            )
            for stat, val in dup.mods.items():
                op = dup.mod_ops.get(stat, "set")
                if op == "set":
                    stats[stat] = val
                elif op == "add":
                    stats[stat] = stats.get(stat, 0.0) + val
                elif op == "multiply":
                    stats[stat] = stats.get(stat, 0.0) * val
            self._plane_cache[key] = stats
            return dict(stats)
        return {}

    # -- aggregation -----------------------------------------------------
    @staticmethod
    def _average_contribution(stat: str, values: List[float]) -> float:
        if not values:
            return 0.0
        # Armor piercing is the average across all relevant weapon modules in
        # the HOI4 ship designer. Using "highest + average of rest" doubles it
        # whenever two identical batteries are fitted.
        if stat in ("lg_armor_piercing", "hg_armor_piercing"):
            return sum(values) / len(values)
        # Other add_average_stats (e.g. module max_strength) keep the
        # highest-plus-average rule used by the designer data.
        ordered = sorted(values, reverse=True)
        if len(ordered) == 1:
            return ordered[0]
        return ordered[0] + sum(ordered[1:]) / len(ordered[1:])

    def _combine(
        self,
        base: Dict[str, float],
        module_names: Iterable[str],
        upgrades: Iterable[Tuple[str, int]],
        design_bonus: Dict[str, float],
        module_table: Dict[str, ModuleDef],
        upgrade_table: Dict[str, UpgradeDef],
        mission_keys: Optional[set] = None,
    ) -> Dict[str, float]:
        add: Dict[str, float] = {}
        mult: Dict[str, float] = {}
        avg_values: Dict[str, List[float]] = {}
        mission_keys = mission_keys or set()

        for name in module_names:
            module = module_table.get(name)
            if module is None:
                continue
            for stat, val in module.add.items():
                add[stat] = add.get(stat, 0.0) + val
            for stat, val in module.multiply.items():
                mult[stat] = mult.get(stat, 0.0) + val
            for stat, val in module.average.items():
                avg_values.setdefault(stat, []).append(val)
            for keys, madd, mmul, mavg in module.missions:
                if not (keys & mission_keys):
                    continue
                for stat, val in madd.items():
                    add[stat] = add.get(stat, 0.0) + val
                for stat, val in mmul.items():
                    mult[stat] = mult.get(stat, 0.0) + val
                for stat, val in mavg.items():
                    avg_values.setdefault(stat, []).append(val)

        for name, level in upgrades:
            upgrade = upgrade_table.get(name)
            if upgrade is None or level <= 0:
                continue
            for stat, per_level in upgrade.per_level.items():
                # Deck space is the only flat upgrade stat; everything else is
                # a percentage modifier.
                if stat in ("carrier_size", "deck_size"):
                    add[stat] = add.get(stat, 0.0) + per_level * level
                else:
                    mult[stat] = mult.get(stat, 0.0) + per_level * level

        for stat, val in design_bonus.items():
            mult[stat] = mult.get(stat, 0.0) + val

        stats = set(base) | set(add) | set(mult) | set(avg_values)
        out: Dict[str, float] = {}
        for stat in stats:
            value = base.get(stat, 0.0) + add.get(stat, 0.0)
            if stat in avg_values:
                value += self._average_contribution(stat, avg_values[stat])
            value *= 1.0 + mult.get(stat, 0.0)
            out[stat] = max(0.0, value)
        return out

    # -- public API ------------------------------------------------------
    def ship_stats(
        self,
        hull_key: str,
        modules: Dict[str, str],
        upgrades: Iterable[Tuple[str, int]],
        design_bonus: Dict[str, float],
    ) -> Dict[str, float]:
        base = self.hull_stats(hull_key)
        module_names = [m for m in modules.values() if m and m != "empty"]
        return self._combine(
            base,
            module_names,
            upgrades,
            design_bonus,
            self.ship_modules,
            self.ship_upgrades,
        )

    def plane_stats(
        self,
        airframe_key: str,
        modules: Dict[str, str],
        upgrades: Iterable[Tuple[str, int]],
        design_bonus: Dict[str, float],
        role: str = "",
    ) -> Dict[str, float]:
        base = self._plane_base_stats(airframe_key)
        module_names = [m for m in modules.values() if m and m != "empty"]
        return self._combine(
            base,
            module_names,
            upgrades,
            design_bonus,
            self.plane_modules,
            self.plane_upgrades,
            mission_keys=plane_mission_keys(role or airframe_key),
        )

    # -- navy-out mapping ------------------------------------------------
    @staticmethod
    def to_navy_ship(stats: Dict[str, float]) -> Dict[str, float]:
        """Map HOI4 equipment stats to navy-out overrides.

        Attack/armor/penetration are always emitted, because a missing HOI4
        stat means zero (e.g. a carrier has no heavy guns). Detection,
        visibility, speed and reliability fall back to navy-out class values
        when HOI4 does not expose a base stat in the equipment files.
        """
        def r(stat: str) -> Optional[float]:
            return round(stats[stat], 2) if stat in stats else None

        out: Dict[str, float] = {}
        for key, stat in (
            ("light_atk", "lg_attack"),
            ("light_pen", "lg_armor_piercing"),
            ("heavy_atk", "hg_attack"),
            ("heavy_pen", "hg_armor_piercing"),
            ("torpedo", "torpedo_attack"),
            ("depth_charge", "sub_attack"),
            ("armor", "armor_value"),
            ("aa", "anti_air_attack"),
        ):
            out[key] = r(stat) or 0.0
        for key, stat in (
            ("speed", "naval_speed"),
            ("surface_vis", "surface_visibility"),
            ("sea_detect", "surface_detection"),
            ("sub_detect", "sub_detection"),
            ("sub_vis", "sub_visibility"),
        ):
            value = r(stat)
            if value is not None:
                out[key] = value
        if "reliability" in stats:
            rel = stats["reliability"]
            out["reliability"] = round(rel * 100.0 if rel <= 1.5 else rel, 1)
        return out

    @staticmethod
    def to_navy_plane(stats: Dict[str, float], plane_type: str) -> Dict[str, float]:
        reliability = stats.get("reliability", 0.0)
        if reliability <= 1.5:
            reliability *= 100.0
        air_combat = (
            stats.get("air_attack", 0.0)
            + stats.get("air_defence", 0.0)
            + stats.get("air_agility", 0.0)
        )
        out = {
            "naval_atk": round(stats.get("naval_strike_attack", 0.0), 2),
            # The requested "air combat capability" is the sum of air attack,
            # air defence and agility.
            "air_atk": round(air_combat, 2),
            "maneuver": round(stats.get("air_agility", 0.0), 2),
            # navy-out needs aim > 1 for damage; HOI4 has no direct equivalent.
            "naval_aim": 4.0 if plane_type == "naval_bomber" else 1.35,
            "sortie_ammo": 1 if plane_type == "naval_bomber" else 0,
        }
        if "maximum_speed" in stats:
            out["speed"] = round(stats["maximum_speed"], 1)
        if "reliability" in stats:
            out["reliability"] = round(reliability, 1)
        return out
