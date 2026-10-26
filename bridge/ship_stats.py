"""Map HOI4 ships and carrier aircraft to navy-out units.

Ship stats come from the exact equipment designer calculation in designer.py:
hull base + module add/multiply/average stats + variant upgrades + design team
bonus. Carrier air wings are read from the save and each wing becomes one
navy-out aircraft; the aircraft stats come from the plane designer (air attack,
air defence, agility, naval strike attack, speed, reliability).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from designer import DesignerDB
from models import AirWing, Ship
from hoi4_save import SaveData


NAVY_TYPE = {
    "carrier": "carrier",
    "battleship": "battleship",
    "battle_cruiser": "battlecruiser",
    "battlecruiser": "battlecruiser",
    "heavy_cruiser": "heavy_cruiser",
    "light_cruiser": "light_cruiser",
    "destroyer": "destroyer",
    "submarine": "submarine",
}

TYPE_NAME_CN = {
    "carrier": "航空母舰",
    "battleship": "战列舰",
    "battlecruiser": "战列巡洋舰",
    "heavy_cruiser": "重型巡洋舰",
    "light_cruiser": "轻型巡洋舰",
    "destroyer": "驱逐舰",
    "submarine": "潜艇",
    "fighter": "战斗机",
    "naval_bomber": "海军轰炸机",
    "naval_recon": "海上侦察机",
}

# Only used for stats HOI4 does not expose in the equipment files (notably
# surface visibility / detection are not hull stats in 1.19). Attack, armor,
# penetration, HP, speed and reliability are always taken from the designer.
NAVY_OUT_DEFAULTS = {
    "carrier": {
        "speed": 30.0, "reliability": 80.0,
        "light_atk": 6.0, "light_pen": 10.0, "heavy_atk": 0.0, "heavy_pen": 0.0,
        "torpedo": 0.0, "depth_charge": 0.0, "armor": 40.0, "aa": 30.0,
        "surface_vis": 18.0, "sea_detect": 20.0, "sub_detect": 6.0, "sub_vis": 0.0,
    },
    "battleship": {
        "speed": 27.0, "reliability": 75.0,
        "light_atk": 12.0, "light_pen": 30.0, "heavy_atk": 90.0, "heavy_pen": 75.0,
        "torpedo": 0.0, "depth_charge": 0.0, "armor": 80.0, "aa": 14.0,
        "surface_vis": 24.0, "sea_detect": 16.0, "sub_detect": 4.0, "sub_vis": 0.0,
    },
    "battlecruiser": {
        "speed": 32.0, "reliability": 72.0,
        "light_atk": 14.0, "light_pen": 32.0, "heavy_atk": 70.0, "heavy_pen": 65.0,
        "torpedo": 0.0, "depth_charge": 0.0, "armor": 55.0, "aa": 16.0,
        "surface_vis": 22.0, "sea_detect": 18.0, "sub_detect": 4.0, "sub_vis": 0.0,
    },
    "heavy_cruiser": {
        "speed": 33.0, "reliability": 78.0,
        "light_atk": 18.0, "light_pen": 26.0, "heavy_atk": 38.0, "heavy_pen": 40.0,
        "torpedo": 24.0, "depth_charge": 0.0, "armor": 35.0, "aa": 20.0,
        "surface_vis": 18.0, "sea_detect": 24.0, "sub_detect": 8.0, "sub_vis": 0.0,
    },
    "light_cruiser": {
        "speed": 35.0, "reliability": 82.0,
        "light_atk": 26.0, "light_pen": 18.0, "heavy_atk": 8.0, "heavy_pen": 15.0,
        "torpedo": 18.0, "depth_charge": 6.0, "armor": 18.0, "aa": 22.0,
        "surface_vis": 14.0, "sea_detect": 30.0, "sub_detect": 12.0, "sub_vis": 0.0,
    },
    "destroyer": {
        "speed": 36.0, "reliability": 85.0,
        "light_atk": 16.0, "light_pen": 12.0, "heavy_atk": 0.0, "heavy_pen": 0.0,
        "torpedo": 34.0, "depth_charge": 40.0, "armor": 8.0, "aa": 18.0,
        "surface_vis": 12.0, "sea_detect": 28.0, "sub_detect": 45.0, "sub_vis": 0.0,
    },
    "submarine": {
        "speed": 18.0, "reliability": 70.0,
        "light_atk": 2.0, "light_pen": 5.0, "heavy_atk": 0.0, "heavy_pen": 0.0,
        "torpedo": 60.0, "depth_charge": 0.0, "armor": 5.0, "aa": 2.0,
        "surface_vis": 10.0, "sea_detect": 26.0, "sub_detect": 6.0, "sub_vis": 3.0,
    },
}


def navy_type(definition: str) -> str:
    return NAVY_TYPE.get(definition, "destroyer")


def has_seaplane_modules(modules) -> bool:
    """Ships with a seaplane catapult or a seaplane-tender module carry one
    abstract naval recon aircraft in navy-out."""
    for module in modules or []:
        name = str(module)
        if name.startswith("ship_airplane_launcher") or name.startswith("ship_seaplane_tender"):
            return True
    return False


def recon_unit() -> Dict[str, object]:
    """A naval recon aircraft. Attributes use navy-out's naval_recon defaults."""
    return {"type": "naval_recon"}


def plane_navy_type(definition: str) -> str:
    d = (definition or "").lower()
    if "nav_bomber" in d or "naval_bomber" in d or "cas" in d or "suicide" in d:
        return "naval_bomber"
    return "fighter"


def prepare_ship(ship: Ship, save: SaveData, designer: DesignerDB) -> Ship:
    """Fill navy-out attributes from the HOI4 ship designer calculation."""
    ship.navy_type = navy_type(ship.definition)
    ship.navy_type_name = TYPE_NAME_CN.get(ship.navy_type, ship.definition)

    design = designer.ship_stats(
        save.variant_key(ship),
        save.modules_for_ship(ship),
        save.upgrades_by_id(ship.equipment_id),
        save.design_bonus_by_id(ship.equipment_id),
    )
    exact = designer.to_navy_ship(design)

    base = dict(NAVY_OUT_DEFAULTS.get(ship.navy_type, NAVY_OUT_DEFAULTS["destroyer"]))
    base.update(exact)
    ship.attributes = base

    computed_hp = design.get("max_strength", 0.0)
    ship.max_strength = max(computed_hp, ship.strength, 1.0)
    ship.start_strength = max(0.0, min(ship.strength, ship.max_strength))

    ship.max_org = save.max_org_for(ship)
    ship.start_org = max(1.0, min(ship.max_org, ship.organisation))
    if not ship.display_name:
        ship.display_name = ship.name
    return ship


def plane_unit(wing: AirWing, save: SaveData, designer: DesignerDB) -> Optional[Dict[str, object]]:
    """One carrier air wing -> one navy-out aircraft."""
    key = save.variant_key_by_id(wing.equipment_id)
    if not key:
        return None
    stats = designer.plane_stats(
        key,
        save.modules_by_id(wing.equipment_id),
        save.upgrades_by_id(wing.equipment_id),
        save.design_bonus_by_id(wing.equipment_id),
        role=wing.definition,
    )
    ptype = plane_navy_type(wing.definition)
    attrs = designer.to_navy_plane(stats, ptype)
    return {"type": ptype, "attributes": attrs}


def disambiguate_names(ships: List[Ship]) -> None:
    """Make display names unique within one side (navy-out reports by name)."""
    counts: Dict[str, int] = defaultdict(int)
    for ship in ships:
        counts[ship.name] += 1
    seen: Dict[str, int] = defaultdict(int)
    for ship in ships:
        if counts[ship.name] <= 1:
            ship.display_name = ship.name
        else:
            seen[ship.name] += 1
            ship.display_name = f"{ship.name}·{seen[ship.name]}"
