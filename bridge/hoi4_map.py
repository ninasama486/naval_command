"""HOI4 map/terrain lookups used to translate a sea province into a navy-out
map preset.

HOI4 stores the naval terrain on the strategic region, not on the province:
`water_shallow_sea`, `water_deep_ocean`, `water_fjords`. That maps almost
one-to-one onto navy-out's `shallow` / `deep` / `fjords` presets.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Optional


NAVAL_TERRAIN_TO_PRESET = {
    "water_shallow_sea": "shallow",
    "water_deep_ocean": "deep",
    "water_fjords": "fjords",
    "water_ocean": "ocean",
    "shallow_sea": "shallow",
    "deep_ocean": "deep",
    "fjords": "fjords",
}

NAVAL_TERRAIN_CN = {
    "water_shallow_sea": "浅海",
    "water_deep_ocean": "深海",
    "water_fjords": "峡湾和群岛",
    "water_ocean": "海洋",
}


@dataclass
class ProvinceInfo:
    province_id: int
    is_sea: bool = False
    terrain: str = ""
    region_id: int = 0
    region_name: str = ""
    naval_terrain: str = ""
    preset: str = "ocean"


@dataclass
class Hoi4Map:
    game_dir: str = ""
    provinces: Dict[int, ProvinceInfo] = field(default_factory=dict)
    region_names: Dict[int, str] = field(default_factory=dict)

    def info(self, province: int) -> ProvinceInfo:
        return self.provinces.get(
            province,
            ProvinceInfo(province_id=province, is_sea=False, terrain="unknown"),
        )

    def is_sea(self, province: int) -> bool:
        return self.info(province).is_sea

    def zone_name(self, province: int) -> str:
        p = self.info(province)
        if p.region_name:
            return p.region_name
        return f"海域 #{province}"

    def terrain_cn(self, province: int) -> str:
        p = self.info(province)
        return NAVAL_TERRAIN_CN.get(p.naval_terrain, p.naval_terrain or p.preset or "海洋")

    def preset(self, province: int) -> str:
        return self.info(province).preset


def _read_loc_file(path: str, out: Dict[str, str]) -> None:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                m = re.match(r'^\s*([A-Za-z0-9_.\-]+)\s*:\s*\d*\s*"(.*)"\s*$', line)
                if m:
                    out[m.group(1)] = m.group(2).replace('\\"', '"')
    except OSError:
        return


def _load_region_names(game_dir: str) -> Dict[int, str]:
    names: Dict[str, str] = {}
    candidates: list[str] = []
    # English first, then Chinese overwrites it, so the Chinese name wins when
    # both exist.
    for lang in ("english", "simp_chinese"):
        candidates.extend(
            glob.glob(
                os.path.join(
                    game_dir,
                    "localisation",
                    lang,
                    "*strategic_region_names*.yml",
                )
            )
        )
    for path in candidates:
        _read_loc_file(path, names)
    result: Dict[int, str] = {}
    for key, value in names.items():
        m = re.match(r"STRATEGICREGION_(\d+)$", key)
        if m:
            result[int(m.group(1))] = value
    return result


def _load_definition(game_dir: str) -> Dict[int, ProvinceInfo]:
    path = os.path.join(game_dir, "map", "definition.csv")
    out: Dict[int, ProvinceInfo] = {}
    if not os.path.exists(path):
        return out
    # The file is small (13k rows); csv is fine, but some rows have quoted
    # fields. A manual split keeps the exact column positions.
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split(";")
            if len(parts) < 7:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            is_sea = parts[4].strip().lower() == "sea"
            out[pid] = ProvinceInfo(
                province_id=pid,
                is_sea=is_sea,
                terrain=parts[6].strip(),
            )
    return out


def _load_regions(game_dir: str, provinces: Dict[int, ProvinceInfo]) -> None:
    pattern = os.path.join(game_dir, "map", "strategicregions", "*.txt")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        id_m = re.search(r"(?m)^\s*id\s*=\s*(\d+)", text)
        if not id_m:
            continue
        region_id = int(id_m.group(1))
        name_m = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
        naval_m = re.search(r"(?m)^\s*naval_terrain\s*=\s*([A-Za-z0-9_]+)", text)
        naval = naval_m.group(1) if naval_m else ""
        # Province list: first `provinces = { ... }` block.
        pm = re.search(r"(?m)^\s*provinces\s*=\s*\{([\s\S]*?)\}", text)
        if not pm:
            continue
        pid_list = [int(x) for x in re.findall(r"\d+", pm.group(1))]
        for pid in pid_list:
            info = provinces.get(pid)
            if info is None:
                info = ProvinceInfo(province_id=pid)
                provinces[pid] = info
            info.region_id = region_id
            info.naval_terrain = naval
            info.preset = NAVAL_TERRAIN_TO_PRESET.get(naval, "ocean")
            if info.is_sea:
                info.terrain = info.terrain or naval


def load_map(game_dir: str, cache_path: Optional[str] = None) -> Hoi4Map:
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            provinces = {
                int(pid): ProvinceInfo(**p) for pid, p in raw.get("provinces", {}).items()
            }
            region_names = {int(k): v for k, v in raw.get("region_names", {}).items()}
            return Hoi4Map(game_dir=game_dir, provinces=provinces, region_names=region_names)
        except (OSError, ValueError, TypeError):
            pass

    provinces = _load_definition(game_dir)
    _load_regions(game_dir, provinces)
    region_names = _load_region_names(game_dir)
    for info in provinces.values():
        if info.region_id and info.region_id in region_names:
            info.region_name = region_names[info.region_id]

    result = Hoi4Map(game_dir=game_dir, provinces=provinces, region_names=region_names)
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            payload = {
                "provinces": {
                    str(pid): {
                        "province_id": p.province_id,
                        "is_sea": p.is_sea,
                        "terrain": p.terrain,
                        "region_id": p.region_id,
                        "region_name": p.region_name,
                        "naval_terrain": p.naval_terrain,
                        "preset": p.preset,
                    }
                    for pid, p in provinces.items()
                },
                "region_names": {str(k): v for k, v in region_names.items()},
            }
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
        except OSError:
            pass
    return result
