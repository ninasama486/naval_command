"""Parser for navy-out's `battle_result.txt` (ExternalApi.result_text)."""

from __future__ import annotations

import re
from typing import List, Tuple

from models import BattleResult, ShipResult


OUTCOME = {"胜利": "victory", "战败": "defeat", "平局": "draw"}

SHIP_LINE = re.compile(
    r"^\s*(?P<title>.+?)\s*\|\s*HP\s*(?P<hp>[0-9.]+)\s*/\s*(?P<max_hp>[0-9.]+)"
    r"\s*\|\s*组织度\s*(?P<org>[0-9.]+)\s*/\s*(?P<max_org>[0-9.]+)"
    r"\s*\|\s*(?P<note>.*)$"
)


def _split_names(value: str) -> List[str]:
    value = value.strip()
    if not value:
        return []
    empty_markers = {"无", "（无）", "(无)", "-", "—", "None", "none"}
    return [
        part.strip()
        for part in value.split("、")
        if part.strip() and part.strip() not in empty_markers
    ]


def _parse_side(lines: List[str]) -> Tuple[List[ShipResult], List[str], List[str]]:
    ships: List[ShipResult] = []
    lost: List[str] = []
    retreated: List[str] = []
    for line in lines:
        m = SHIP_LINE.match(line)
        if m:
            ships.append(
                ShipResult(
                    title=m.group("title").strip(),
                    hp=float(m.group("hp")),
                    max_hp=float(m.group("max_hp")),
                    org=float(m.group("org")),
                    max_org=float(m.group("max_org")),
                    status="remaining",
                    in_hangar="机库内" in m.group("note"),
                )
            )
            continue
        if line.strip().startswith("已损失："):
            lost.extend(_split_names(line.split("：", 1)[1]))
        elif line.strip().startswith("已撤离："):
            retreated.extend(_split_names(line.split("：", 1)[1]))
    for title in lost:
        ships.append(ShipResult(title=title, status="lost"))
    for title in retreated:
        ships.append(ShipResult(title=title, status="retreated"))
    return ships, lost, retreated


def parse_result_text(text: str) -> BattleResult:
    result = BattleResult(raw_text=text)
    lines = text.splitlines()
    section = None
    player_lines: List[str] = []
    enemy_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("胜负："):
            result.outcome = OUTCOME.get(stripped.split("：", 1)[1].strip(), "unfinished")
        elif stripped.startswith("回合数："):
            try:
                result.turns = int(float(stripped.split("：", 1)[1].strip()))
            except ValueError:
                pass
        elif stripped.startswith("结束天气："):
            result.weather = stripped.split("：", 1)[1].strip()
        elif stripped.startswith("地图预设："):
            result.preset = stripped.split("：", 1)[1].strip()
        elif stripped.startswith("---------------- 我方"):
            section = "player"
        elif stripped.startswith("---------------- 敌方"):
            section = "enemy"
        elif stripped.startswith("================"):
            if "结束" in stripped:
                section = None
        else:
            if section == "player":
                player_lines.append(line)
            elif section == "enemy":
                enemy_lines.append(line)

    result.player, result.player_lost, result.player_retreated = _parse_side(player_lines)
    result.enemy, result.enemy_lost, result.enemy_retreated = _parse_side(enemy_lines)
    return result


def is_complete(text: str) -> bool:
    return "================ 结束" in text and "胜负：" in text
