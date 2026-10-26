"""Shared data structures for the HOI4 -> navy-out -> HOI4 bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Ship:
    """One HOI4 ship, as read from the save."""

    ship_id: int
    definition: str
    organisation: float
    strength: float
    name: str
    equipment_id: int
    tag: str
    task_force_id: int
    task_force_name: str
    location: int
    name_override: str = ""
    name_order: int = -1
    name_group: str = ""
    name_prefix: str = ""
    max_org: float = 0.0
    max_strength: float = 0.0
    navy_type: str = ""
    navy_type_name: str = ""
    display_name: str = ""
    attributes: Dict[str, float] = field(default_factory=dict)
    # Battle-start values, filled from the scan snapshot.
    start_strength: float = 0.0
    start_org: float = 0.0

    def __post_init__(self) -> None:
        if self.max_strength <= 0.0:
            self.max_strength = self.strength
        if self.start_strength == 0.0:
            self.start_strength = self.strength
        if self.start_org == 0.0:
            self.start_org = self.organisation


@dataclass
class TaskForce:
    task_force_id: int
    name: str
    location: int
    tag: str
    last_combat_date: str
    fleet_name: str
    ships: List[Ship] = field(default_factory=list)


@dataclass
class AirWing:
    """One carrier air wing/squadron, mapped to one navy-out aircraft."""

    wing_id: int
    carrier_ship_id: int
    definition: str
    count: int
    equipment_id: int
    tag: str = ""
    name: str = ""


@dataclass
class Fleet:
    name: str
    task_forces: List[TaskForce] = field(default_factory=list)

    @property
    def ships(self) -> List[Ship]:
        return [s for tf in self.task_forces for s in tf.ships]


@dataclass
class BattleCandidate:
    province: int
    player_tag: str
    enemy_tag: str
    player_ships: List[Ship]
    enemy_ships: List[Ship]
    zone_name: str = ""
    region_name: str = ""
    naval_terrain: str = ""
    preset: str = "ocean"
    active: bool = False
    last_combat_date: str = ""
    score: float = 0.0
    summary: str = ""

    def title(self) -> str:
        status = "交战中" if self.active else "海域对峙"
        return (
            f"{self.zone_name or ('海域 #' + str(self.province))}"
            f"（{self.naval_terrain or self.preset}） · {status}"
            f" · 我方 {len(self.player_ships)} 艘 vs {self.enemy_tag} {len(self.enemy_ships)} 艘"
        )


@dataclass
class ShipResult:
    title: str
    hp: float = 0.0
    max_hp: float = 0.0
    org: float = 0.0
    max_org: float = 0.0
    status: str = "unknown"  # remaining | lost | retreated
    in_hangar: bool = False


@dataclass
class BattleResult:
    outcome: str = "unfinished"
    turns: int = 0
    weather: str = ""
    preset: str = ""
    player: List[ShipResult] = field(default_factory=list)
    enemy: List[ShipResult] = field(default_factory=list)
    player_lost: List[str] = field(default_factory=list)
    enemy_lost: List[str] = field(default_factory=list)
    player_retreated: List[str] = field(default_factory=list)
    enemy_retreated: List[str] = field(default_factory=list)
    raw_text: str = ""

    def outcome_cn(self) -> str:
        return {
            "victory": "胜利",
            "defeat": "战败",
            "draw": "平局",
            "unfinished": "未分胜负",
        }.get(self.outcome, self.outcome)


@dataclass
class BattleContext:
    """Everything needed to build the setup and later write back the result."""

    battle: BattleCandidate
    player_tag: str
    enemy_tag: str
    province: int
    zone_name: str
    naval_terrain: str
    preset: str
    player_ships: List[Ship]
    enemy_ships: List[Ship]
    player_aircraft: List[Dict[str, object]] = field(default_factory=list)
    enemy_aircraft: List[Dict[str, object]] = field(default_factory=list)
    setup_path: str = ""
    output_path: str = ""
    seed: int = 0
    player_doctrine: str = ""
    enemy_doctrine: str = ""
    player_navy_techs: int = 0
    enemy_navy_techs: int = 0

    def all_ships(self) -> List[Ship]:
        return list(self.player_ships) + list(self.enemy_ships)


@dataclass
class WritebackPlan:
    player_tag: str
    enemy_tag: str
    province: int
    player_org_damage: float = 0.0
    player_str_damage: float = 0.0
    enemy_org_damage: float = 0.0
    enemy_str_damage: float = 0.0
    player_sunk: Dict[str, int] = field(default_factory=dict)  # HOI4 definition -> count
    enemy_sunk: Dict[str, int] = field(default_factory=dict)
    player_survivors: int = 0
    enemy_survivors: int = 0
    all_retreated: bool = False
    player_all_retreated: bool = False
    enemy_all_retreated: bool = False
    player_lost_names: List[str] = field(default_factory=list)
    enemy_lost_names: List[str] = field(default_factory=list)
    summary: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class BattleSession:
    context: BattleContext
    result: Optional[BattleResult] = None
    plan: Optional[WritebackPlan] = None
    process: object = None
    started_at: float = 0.0
