"""Offline self-test for the navy-command bridge.

Runs without HOI4 or navy-out:
  * map/terrain lookup
  * save parsing (synthetic fleet, war relation)
  * battle discovery
  * navy-out setup generation
  * result parsing
  * writeback plan and console command syntax
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import battle as battle_mod
import hoi4_commands
import hoi4_save
import result_parser
import ship_names
from config import load_config
from hoi4_map import load_map
from models import BattleResult, ShipResult
from ship_stats import has_seaplane_modules


SAMPLE_SAVE = """HOI4txt
player="ITA"
date="1936.1.1.12"
equipments={
	ship_hull_light_1={
		id={ id=1 type=70 }
		max_version=0
		is_frame=no
		creator="ITA"
		origin="---"
	}
}
ITA={
	technology={
		technologies={
			basic_ship_hull_light={
				level=1
				research_points=165
			}
			basic_light_battery={
				level=1
				research_points=110
			}
		}
	}
}
fleet={
	id={ id=1 type=61 }
	name="Test Fleet"
	task_force={
		id={ id=1 type=51 }
		name="TF 1"
		last_combat_date="1936.1.1.12"
		location=2918
		logical_country="ITA"
		ship={
			id={ id=10 type=51 }
			definition=destroyer
			organisation=35
			strength=100
			equipment={
				equipment={
					id={ id=1 type=70 }
					amount=1
				}
				allow_zero_entries=no
			}
			ship_name={
				type=1
				name_order=0
				is_name_ordered=no
			}
		}
	}
}
fleet={
	id={ id=2 type=61 }
	name="Enemy Fleet"
	task_force={
		id={ id=2 type=51 }
		name="TF 2"
		last_combat_date="1936.1.1.12"
		location=2918
		logical_country="ENG"
		ship={
			id={ id=20 type=51 }
			definition=destroyer
			organisation=35
			strength=100
			equipment={
				equipment={
					id={ id=1 type=70 }
					amount=1
				}
				allow_zero_entries=no
			}
			ship_name={
				type=1
				override="HMS Test"
			}
		}
	}
}
war_relation={
	first="ITA"
	second="ENG"
	start_date="1936.1.1.12"
}
"""

SAMPLE_RESULT = """================ 海战结果 ================
胜负：胜利
回合数：4
结束天气：晴天
地图预设：浅海预设（陆地 0% / 浅海 100% / 深海 0%）

---------------- 我方 ----------------
剩余单位：1（损失 0，撤离 0）
  单位                         HP           组织度        备注
  destroyer #10-驱逐舰 | HP 75/150 | 组织度 17/35 | 驱逐舰

---------------- 敌方 ----------------
剩余单位：0（损失 1，撤离 0）
  （无）
  已损失：HMS Test-驱逐舰

================ 结束 ================
"""


class Tester:
    def __init__(self) -> None:
        self.failures = 0
        self.checks = 0

    def check(self, condition: bool, label: str) -> None:
        self.checks += 1
        if condition:
            print(f"  OK   {label}")
        else:
            self.failures += 1
            print(f"  FAIL {label}")


def main() -> int:
    cfg = load_config()
    t = Tester()
    print("0) 玩家设置")
    import config as config_mod

    merged = config_mod._merge_player_settings(
        {"hoi4_user_dir": "old", "selection": "event"},
        {"hoi4_user_dir": "", "_说明": "comments are ignored"},
    )
    t.check(
        merged["hoi4_user_dir"] == "" and merged["selection"] == "event",
        "settings.json overrides config and ignores comment keys",
    )
    t.check(
        config_mod._resolve_path("navy-out/navy.exe")
        == os.path.normpath(os.path.join(config_mod.NAVY_DIR, "navy-out", "navy.exe")),
        "relative player paths resolve against project root",
    )
    t.check(
        os.path.isdir(config_mod._detect_mod_source(cfg.hoi4_user_dir)),
        "mod source auto-detected from project or installed .mod",
    )

    print("0b) 便携启动")
    import launch_game

    portable_root = tempfile.mkdtemp(prefix="ntc_portable_")
    try:
        user_dir = os.path.join(portable_root, "user")
        source_dir = os.path.join(portable_root, "mod-source")
        os.makedirs(os.path.join(user_dir, "mod"))
        os.makedirs(source_dir)
        launch_game.sanitize_local_mod(user_dir, mod_source_dir=source_dir)
        mod_file = os.path.join(user_dir, "mod", "naval-tactical-command.mod")
        text = open(mod_file, encoding="utf-8").read()
        t.check(
            'path="' + source_dir.replace("\\", "/") + '"' in text,
            "launcher creates .mod descriptor for a mod source",
        )
    finally:
        shutil.rmtree(portable_root, ignore_errors=True)

    print("1) 地图/海区")
    mapdata = load_map(cfg.hoi4_game_dir, cfg.map_cache)
    t.check(mapdata.is_sea(2918), "province 2918 is sea")
    t.check(mapdata.preset(2918) in ("shallow", "deep", "fjords", "ocean"), "preset resolved")
    t.check(bool(mapdata.zone_name(2918)), "zone has a name")

    print("2) 存档解析")
    tmp = os.path.join(tempfile.gettempdir(), "navy_command_selftest.hoi4")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_SAVE)
    save = hoi4_save.load_save(tmp)
    t.check(save.player_tag == "ITA", "player tag")
    t.check(len(save.ships) == 2, "two deployed ships")
    t.check(save.at_war("ITA", "ENG"), "war pair parsed")
    t.check(save.max_org("destroyer") >= 35, "destroyer max org")
    t.check(all(s.max_strength >= 100 for s in save.ships), "max strength")

    print("3) 战斗发现")
    candidates = battle_mod.find_candidates(save, mapdata, max_battles=3)
    t.check(len(candidates) == 1, "one candidate")
    cand = candidates[0]
    t.check(cand.player_tag == "ITA" and cand.enemy_tag == "ENG", "candidate sides")
    t.check(len(cand.player_ships) == 1 and len(cand.enemy_ships) == 1, "candidate ships")
    t.check(cand.active, "recent combat marks active")

    print("3b) 战斗开始时间 -> 昼夜")
    t.check(battle_mod.date_hour("1936.1.15.22") == 22, "hour parsed from save date")
    t.check(battle_mod.date_hour("1936.1.15") == -1, "missing hour -> -1")
    t.check(battle_mod.day_night_for("1936.1.1.7") == "夜晚", "07:00 is night")
    t.check(battle_mod.day_night_for("1936.1.1.8") == "白天", "08:00 is day")
    t.check(battle_mod.day_night_for("1936.1.1.12") == "白天", "12:00 is day")
    t.check(battle_mod.day_night_for("1936.1.1.16") == "白天", "16:00 is day")
    t.check(battle_mod.day_night_for("1936.1.1.17") == "夜晚", "17:00 is night")
    t.check(battle_mod.day_night_for("1936.1.1.2") == "夜晚", "02:00 is night")

    print("4) navy-out 开局数据")
    from designer import DesignerDB

    designer = DesignerDB(cfg.hoi4_game_dir)
    t.check(len(designer.ship_hulls) > 0, "ship designer hulls loaded")
    t.check(len(designer.plane_airframes) > 0, "plane designer airframes loaded")
    engine = designer.ship_modules.get("heavy_ship_engine_2")
    t.check(engine is not None, "engine module loaded")
    if engine is not None:
        t.check(
            abs(engine.add.get("naval_speed", 0.0)) < 1e-9,
            "commented-out engine naval_speed is ignored",
        )
        t.check(
            abs(engine.multiply.get("naval_speed", 0.0) - 0.35) < 1e-6,
            "engine speed multiplier parsed",
        )
    two_guns = designer.ship_stats(
        "ship_hull_cruiser_2",
        {
            "fixed_ship_battery_slot": "ship_light_medium_battery_1",
            "mid_1_custom_slot": "ship_light_medium_battery_1",
        },
        [],
        {},
    )
    t.check(
        abs(two_guns.get("lg_armor_piercing", 0.0) - 5.5) < 0.01,
        "two identical guns do not double penetration",
    )
    t.check(
        abs(two_guns.get("lg_attack", 0.0) - 8.0) < 0.01,
        "two batteries still add attack",
    )
    t.check(
        has_seaplane_modules(["ship_airplane_launcher_1"]),
        "seaplane catapult adds recon",
    )
    t.check(
        has_seaplane_modules(["ship_seaplane_tender_2"]),
        "seaplane tender adds recon",
    )
    t.check(
        not has_seaplane_modules(["ship_light_battery_4"]),
        "ordinary gun does not add recon",
    )
    context, setup = battle_mod.build_context(
        cand, save, mapdata, cfg.session_dir, designer, with_carrier_air=True
    )
    t.check(setup["map_preset"] in ("shallow", "deep", "fjords", "ocean"), "map preset in setup")
    t.check(setup["day_night"] == "白天", "day_night follows 12:00 battle start")
    t.check(len(setup["player"]["units"]) == 1, "one player unit")
    t.check(len(setup["enemy"]["units"]) == 1, "one enemy unit")
    t.check(setup["player"]["units"][0]["type"] == "destroyer", "unit type")
    t.check(setup["player"]["units"][0]["max_hp"] >= 100.0, "HP from designer/current strength")
    t.check("attributes" in setup["player"]["units"][0], "exact attributes present")
    setup_path = os.path.join(cfg.session_dir, "selftest_setup.json")
    with open(setup_path, "w", encoding="utf-8") as fh:
        import json

        json.dump(setup, fh, ensure_ascii=False, indent=2)
    t.check(os.path.exists(setup_path), "setup written")

    print("5) 结果解析")
    result = result_parser.parse_result_text(SAMPLE_RESULT)
    t.check(result.outcome == "victory", "outcome")
    t.check(result.turns == 4, "turns")
    t.check(len(result.player) == 1, "player result")
    t.check(result.player[0].hp == 75.0, "player HP")
    t.check(result.enemy_lost == ["HMS Test-驱逐舰"], "enemy sunk list")
    t.check(result_parser.is_complete(SAMPLE_RESULT), "complete result")

    print("6) 回写计划")
    plan = battle_mod.build_writeback(context, result)
    t.check(plan.player_tag == "ITA" and plan.enemy_tag == "ENG", "plan sides")
    t.check(plan.enemy_sunk.get("destroyer") == 1, "sunk count by type")
    t.check(plan.player_survivors == 1 and plan.enemy_survivors == 0, "survivor counts")
    t.check(0.4 < plan.player_str_damage < 0.6, "player strength damage ~50%")
    t.check(0.4 < plan.player_org_damage < 0.6, "player org damage ~50%")
    commands = hoi4_commands.writeback_commands(plan)
    joined = "\n".join(commands)
    t.check("destroy_ships" in joined, "destroy_ships emitted")
    t.check("damage_units" in joined and "navy = yes" in joined, "damage_units navy emitted")
    t.check("ntc_" in hoi4_commands.set_vars_command("ITA", {"ntc_b1": "x"}), "variables command")

    print("6b) 全员主动撤离")
    retreat_result = BattleResult(
        outcome="draw",
        player=[ShipResult(title="destroyer #10-驱逐舰", status="retreated")],
        enemy=[ShipResult(title="HMS Test-驱逐舰", status="retreated")],
        player_retreated=["destroyer #10-驱逐舰"],
        enemy_retreated=["HMS Test-驱逐舰"],
    )
    quiet_plan = battle_mod.build_writeback(context, retreat_result, retreat_action="none")
    t.check(quiet_plan.all_retreated, "all-retreat detected")
    t.check(
        sum(quiet_plan.player_sunk.values()) == 0
        and sum(quiet_plan.enemy_sunk.values()) == 0,
        "all-retreat causes no sink",
    )
    t.check(
        quiet_plan.player_org_damage == 0.0
        and quiet_plan.player_str_damage == 0.0
        and quiet_plan.enemy_org_damage == 0.0
        and quiet_plan.enemy_str_damage == 0.0,
        "retreat_action none applies no damage",
    )
    quiet_cmds = "\n".join(hoi4_commands.writeback_commands(quiet_plan))
    t.check(
        "destroy_ships" not in quiet_cmds and "damage_units" not in quiet_cmds,
        "retreat_action none emits no loss/damage commands",
    )
    org_plan = battle_mod.build_writeback(context, retreat_result, retreat_action="org")
    t.check(
        org_plan.player_org_damage == 1.0
        and org_plan.enemy_org_damage == 1.0
        and org_plan.player_str_damage == 0.0
        and org_plan.enemy_str_damage == 0.0,
        "breakoff zeroes org but not HP",
    )
    org_cmds = "\n".join(hoi4_commands.writeback_commands(org_plan))
    t.check(
        "org_damage = 1.000" in org_cmds
        and "str_damage" not in org_cmds
        and "destroy_ships" not in org_cmds,
        "breakoff command is org-only",
    )

    one_side_result = BattleResult(
        outcome="defeat",
        player=[ShipResult(title="destroyer #10-驱逐舰", status="retreated")],
        enemy=[
            ShipResult(
                title="HMS Test-驱逐舰",
                hp=150,
                max_hp=150,
                org=35,
                max_org=35,
                status="remaining",
            )
        ],
        player_retreated=["destroyer #10-驱逐舰"],
    )
    one_side_plan = battle_mod.build_writeback(
        context, one_side_result, retreat_action="org"
    )
    t.check(
        one_side_plan.player_all_retreated
        and not one_side_plan.enemy_all_retreated
        and not one_side_plan.all_retreated,
        "one-sided retreat detected",
    )
    t.check(
        one_side_plan.player_org_damage == 1.0
        and one_side_plan.enemy_org_damage == 0.0
        and one_side_plan.player_str_damage == 0.0
        and one_side_plan.enemy_str_damage == 0.0,
        "only the retreating side loses org",
    )
    one_side_cmds = "\n".join(hoi4_commands.writeback_commands(one_side_plan))
    t.check(
        "tag = ITA" in one_side_cmds
        and "tag = ENG" not in one_side_cmds
        and "destroy_ships" not in one_side_cmds,
        "one-sided breakoff targets only the retreating side",
    )

    air_loss_result = BattleResult(
        outcome="draw",
        player=[ShipResult(title="destroyer #10-驱逐舰", status="retreated")],
        enemy=[ShipResult(title="HMS Test-驱逐舰", status="retreated")],
        player_lost=["战斗机 #1"],
        player_retreated=["destroyer #10-驱逐舰"],
        enemy_retreated=["HMS Test-驱逐舰"],
    )
    air_loss_plan = battle_mod.build_writeback(
        context, air_loss_result, retreat_action="org"
    )
    t.check(
        air_loss_plan.all_retreated
        and not any(air_loss_plan.player_sunk.values())
        and not any(air_loss_plan.enemy_sunk.values()),
        "aircraft losses do not block a clean ship retreat",
    )

    # Unmatched result titles must still destroy ships by type and force a
    # full-damage line on a wiped side; otherwise the vanilla battle continues.
    enemy_title = f"{context.enemy_ships[0].display_name}-{context.enemy_ships[0].navy_type_name}"
    bad = BattleResult(
        outcome="defeat",
        player=[],
        player_lost=["Unknown Name-驱逐舰"],
        enemy=[ShipResult(title=enemy_title, hp=150, max_hp=150, org=35, max_org=35, status="remaining")],
    )
    bad_plan = battle_mod.build_writeback(context, bad)
    t.check(bad_plan.player_sunk.get("destroyer") == 1, "unmatched loss still sinks by type")
    t.check(
        bad_plan.player_org_damage == 1.0 and bad_plan.player_str_damage == 1.0,
        "wiped side gets full damage",
    )
    bad_joined = "\n".join(hoi4_commands.writeback_commands(bad_plan))
    t.check("destroy_ships" in bad_joined, "wiped side destroy command emitted")
    t.check("type = ship_hull_light" in bad_joined, "destroy uses hull archetype")
    t.check(
        "org_damage = 1.000" in bad_joined and "str_damage = 1.000" in bad_joined,
        "wiped side full damage command emitted",
    )

    print("7) 舰名库")
    groups = ship_names.load_name_groups(cfg.hoi4_game_dir)
    t.check(len(groups) > 100, "name groups loaded")
    italian = groups.get("ITA_BB_HISTORICAL")
    t.check(italian is not None and "Caio Duilio" in italian.names, "Italian BB names")

    os.remove(tmp)
    print(f"\n{t.checks} 项检查，{t.failures} 项失败。")
    return 0 if t.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
