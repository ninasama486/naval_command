"""navy-command bridge: HOI4 save -> navy-out -> HOI4 writeback.

ASCII-only source on purpose: this file has been corrupted once by a
PowerShell Get-Content/Set-Content round-trip. Keep non-ASCII text in the
localisation files or in the data returned by the game.

Run with:  python navy_command_bridge.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from typing import Dict, List, Optional

# Make sibling imports work when launched from any cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import battle as battle_mod
import hoi4_commands
import hoi4_save
import navy_out
import result_parser
import ship_names
from config import Config, load_config, validate
from designer import DesignerDB
from hoi4_console import Hoi4Injector, InjectionError
from hoi4_map import Hoi4Map, load_map
from log_tail import LogTail
from models import BattleCandidate, BattleSession


BANNER = r"""
==============================================================
  navy-command  |  HOI4 naval tactical command bridge
  strategic fleet -> navy-out battle -> org/HP writeback
==============================================================
"""


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def message_box(text: str, title: str = "navy-command") -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        pass


def error_box(text: str, title: str = "navy-command error") -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


class Bridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.injector = Hoi4Injector(
            cfg.hoi4_user_dir, cfg.game_log, dry_run=cfg.inject_dry_run
        )
        self.tail = LogTail(cfg.game_log, from_start=cfg.log_from_start)
        self.map: Optional[Hoi4Map] = None
        self.name_groups = {}
        self.designer: Optional[DesignerDB] = None
        self.state = "idle"  # idle | awaiting_pick | in_battle
        self.pending: List[BattleCandidate] = []
        self.pending_save = None
        self.pending_tag = ""
        self.session: Optional[BattleSession] = None
        self.abort_requested = False
        self.awaiting_since = 0.0
        self._last_wait_warning = 0.0

    # ------------------------------------------------------------------
    # Startup / diagnostics
    # ------------------------------------------------------------------
    def load_world(self) -> None:
        log(f"HOI4 user dir: {self.cfg.hoi4_user_dir}")
        log(f"HOI4 game dir: {self.cfg.hoi4_game_dir}")
        log(f"navy-out: {self.cfg.navy_out_exe}")
        log(f"game.log: {self.cfg.game_log}")
        log("Loading map, ship names and equipment designer data...")
        t0 = time.time()
        self.map = load_map(self.cfg.hoi4_game_dir, self.cfg.map_cache)
        self.name_groups = ship_names.load_name_groups(self.cfg.hoi4_game_dir)
        self.designer = DesignerDB(self.cfg.hoi4_game_dir)
        log(
            f"Loaded {len(self.map.provinces)} provinces, "
            f"{len(self.name_groups)} name groups, "
            f"{len(self.designer.ship_hulls)} ship hulls, "
            f"{len(self.designer.ship_modules)} ship modules, "
            f"{len(self.designer.plane_airframes)} plane airframes, "
            f"{len(self.designer.plane_modules)} plane modules "
            f"in {time.time() - t0:.1f}s"
        )

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------
    def _parse_snapshot(self, path: str):
        log(f"Parsing save: {os.path.basename(path)} ({os.path.getsize(path) / 1e6:.1f} MB)")
        t0 = time.time()
        save = hoi4_save.load_save(path)
        resolved = ship_names.resolve_ship_names(save, self.name_groups)
        log(
            f"Parsed: {hoi4_save.save_summary(save)}; "
            f"resolved {resolved} ship names in {time.time() - t0:.1f}s"
        )
        return save

    def run_scan(self, tag_from_log: str) -> None:
        if self.map is None or self.designer is None:
            self.load_world()
        log("Scan requested: pausing game and writing a snapshot...")
        try:
            path = self.injector.inject_save(self.cfg.save_name)
        except (InjectionError, OSError) as exc:
            log(f"Scan failed: {exc}")
            error_box(
                "Could not make HOI4 write a save. Make sure the game is running, "
                "not minimized, and currently in-game.\n\n" + str(exc)
            )
            return

        try:
            save = self._parse_snapshot(path)
        except Exception as exc:
            log(f"Save parse failed: {exc}")
            self._fire_quiet(tag_from_log or "")
            return

        tag = save.player_tag or tag_from_log
        if tag_from_log and tag and tag_from_log != tag:
            log(f"Note: log tag {tag_from_log} != save player {tag}; using save player.")

        assert self.map is not None
        candidates = battle_mod.find_candidates(
            save, self.map, max_battles=self.cfg.max_battles
        )
        if not candidates:
            log("No naval battle candidates found.")
            self._fire_quiet(tag)
            return

        log(f"Found {len(candidates)} candidate battle(s):")
        for idx, cand in enumerate(candidates, start=1):
            log(f"  {idx}. {cand.summary} (province #{cand.province})")

        self.pending = candidates
        self.pending_save = save
        self.pending_tag = tag
        self.abort_requested = False

        if self.cfg.selection == "dialog":
            choice = self._dialog_pick(candidates)
            if choice is None:
                self._fire_quiet(tag)
                return
            self._start_battle(choice)
            return

        commands = hoi4_commands.scan_event_commands(tag, candidates)
        try:
            self.injector.inject(commands)
        except InjectionError as exc:
            log(f"Could not open the in-game picker: {exc}")
            error_box(
                "The scan succeeded, but the picker event could not be injected.\n"
                "Set selection=dialog in settings.json, or check console injection.\n\n"
                + str(exc)
            )
            self._fire_quiet(tag)
            return
        self.state = "awaiting_pick"
        self.awaiting_since = time.time()
        log("Picker event opened; waiting for the player's choice...")

    def _fire_quiet(self, tag: str) -> None:
        if not tag:
            return
        try:
            self.injector.inject(hoi4_commands.abort_commands(tag))
        except InjectionError as exc:
            log(f"Could not clear the scan state: {exc}")

    def _abort_game_side(self, tag: str) -> None:
        if not tag:
            return
        try:
            self.injector.inject(
                [
                    f"eval_effect {tag} = {{ country_event = {{ id = ntc_abort.1 hours = 0 }} }}",
                    "gamespeed 0",
                ]
            )
        except InjectionError as exc:
            log(f"Could not clear tactical mode in HOI4: {exc}")

    def _dialog_pick(self, candidates: List[BattleCandidate]) -> Optional[BattleCandidate]:
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception:
            print("\nAvailable battles:")
            for idx, cand in enumerate(candidates, start=1):
                print(f"  {idx}. {cand.summary} (province #{cand.province})")
            try:
                value = input("Pick a number (blank = cancel): ").strip()
            except EOFError:
                return None
            if not value.isdigit():
                return None
            idx = int(value)
            return candidates[idx - 1] if 1 <= idx <= len(candidates) else None

        result = {"choice": None}
        root = tk.Tk()
        root.title("navy-command - choose a naval battle")
        root.geometry("820x460")
        root.attributes("-topmost", True)

        tk.Label(
            root,
            text="HOI4 is paused. Choose a battle for navy-out:",
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 6))

        frame = tk.Frame(root)
        frame.pack(fill="both", expand=True, padx=12, pady=6)
        listbox = tk.Listbox(frame, font=("Microsoft YaHei UI", 10), height=10)
        listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        scroll.pack(side="right", fill="y")
        listbox.config(yscrollcommand=scroll.set)

        detail = tk.Text(root, height=6, font=("Microsoft YaHei UI", 9), wrap="word")
        detail.pack(fill="x", padx=12, pady=(0, 6))

        for cand in candidates:
            listbox.insert("end", cand.summary)

        def update_detail(_event=None):
            detail.delete("1.0", "end")
            sel = listbox.curselection()
            if not sel:
                return
            cand = candidates[sel[0]]
            names = ", ".join(s.display_name or s.name for s in cand.player_ships[:18])
            if len(cand.player_ships) > 18:
                names += ", ..."
            detail.insert(
                "end",
                f"{cand.zone_name} / {cand.naval_terrain} / province #{cand.province}\n"
                f"Player {len(cand.player_ships)} ships: {names}\n"
                f"Enemy {cand.enemy_tag} {len(cand.enemy_ships)} ships / "
                f"{'in combat' if cand.active else 'contested waters'}",
            )

        listbox.bind("<<ListboxSelect>>", update_detail)
        listbox.selection_set(0)
        update_detail()

        def choose():
            sel = listbox.curselection()
            if sel:
                result["choice"] = candidates[sel[0]]
                root.destroy()

        buttons = tk.Frame(root)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(
            buttons, text="Take command", command=choose, width=18,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="right")
        tk.Button(
            buttons, text="Cancel", command=root.destroy, width=10,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="right", padx=8)
        listbox.bind("<Double-Button-1>", lambda _e: choose())
        root.mainloop()
        return result["choice"]

    # ------------------------------------------------------------------
    # Battle
    # ------------------------------------------------------------------
    def _start_battle(self, cand: BattleCandidate) -> None:
        save = self.pending_save
        if save is None or self.map is None:
            log("Internal error: missing save snapshot.")
            return
        if self.designer is None:
            self.designer = DesignerDB(self.cfg.hoi4_game_dir)

        try:
            context, setup = battle_mod.build_context(
                cand,
                save,
                self.map,
                self.cfg.session_dir,
                self.designer,
                with_carrier_air=self.cfg.carrier_air_wings,
            )
        except Exception as exc:
            log(f"Building battle data failed: {exc}")
            traceback.print_exc()
            error_box("Building battle data failed:\n" + str(exc))
            self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return

        battle_mod.dump_context(context, os.path.join(self.cfg.session_dir, "context.json"))
        self.session = BattleSession(context=context)
        self.state = "in_battle"
        self.abort_requested = False

        log(f"Taking command of battle {self.pending.index(cand) + 1}: {cand.summary}")

        def log_ship(ship) -> None:
            a = ship.attributes
            log(
                f"    {ship.display_name} ({ship.definition}) "
                f"HP {ship.start_strength:.1f}/{ship.max_strength:.1f} "
                f"org {ship.start_org:.1f}/{ship.max_org:.1f} | "
                f"light {a.get('light_atk', 0):.1f}/{a.get('light_pen', 0):.1f} "
                f"heavy {a.get('heavy_atk', 0):.1f}/{a.get('heavy_pen', 0):.1f} "
                f"torp {a.get('torpedo', 0):.1f} armor {a.get('armor', 0):.1f} "
                f"AA {a.get('aa', 0):.1f} speed {a.get('speed', 0):.1f}"
            )

        log("Player fleet:")
        for ship in context.player_ships:
            log_ship(ship)
        log(f"Player carrier air wings: {len(context.player_aircraft)}")
        log("Enemy fleet:")
        for ship in context.enemy_ships:
            log_ship(ship)
        log(f"Enemy carrier air wings: {len(context.enemy_aircraft)}")

        if self.cfg.selection == "dialog":
            try:
                self.injector.inject(hoi4_commands.mode_on_commands(cand.player_tag))
            except InjectionError as exc:
                log(f"Could not enter tactical mode: {exc}")
                error_box("Could not enter tactical mode:\n" + str(exc))
                self._reset_battle()
                return

        log(f"Starting navy-out: {self.cfg.navy_out_exe}")
        try:
            text, proc = navy_out.launch_and_wait(
                self.cfg.navy_out_exe,
                self.cfg.navy_out_workdir,
                setup,
                context.setup_path,
                context.output_path,
                timeout=7200.0,
                on_tick=self._battle_tick,
            )
        except navy_out.NavyOutError as exc:
            log(f"navy-out error: {exc}")
            if self.abort_requested:
                log("Player aborted the tactical battle; no writeback.")
            else:
                error_box(
                    "navy-out did not finish normally. No result was written back.\n"
                    "Use the in-game Abandon/Cancel decision if tactical mode is stuck.\n\n"
                    + str(exc)
                )
                self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return
        except Exception as exc:
            log(f"Starting navy-out failed: {exc}")
            traceback.print_exc()
            error_box("Starting navy-out failed:\n" + str(exc))
            self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return

        self.session.process = proc
        try:
            result = result_parser.parse_result_text(text)
            self.session.result = result
            log(
                f"Battle finished: {result.outcome_cn()}, {result.turns} turns; "
                f"player losses {len(result.player_lost)}, enemy losses {len(result.enemy_lost)}."
            )
            plan = battle_mod.build_writeback(
                context, result, retreat_action=self.cfg.retreat_action
            )
            self.session.plan = plan
        except Exception as exc:
            log(f"Parsing result failed: {exc}")
            traceback.print_exc()
            error_box("Battle finished, but the result file could not be parsed.\n\n" + str(exc))
            self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return

        if plan.warnings:
            for warning in plan.warnings:
                log("  warning: " + warning)

        log(
            "Writeback plan: player sunk %s, enemy sunk %s; "
            "player org/str %.3f/%.3f, enemy org/str %.3f/%.3f; "
            "retreated player/enemy=%s/%s"
            % (
                plan.player_sunk,
                plan.enemy_sunk,
                plan.player_org_damage,
                plan.player_str_damage,
                plan.enemy_org_damage,
                plan.enemy_str_damage,
                plan.player_all_retreated,
                plan.enemy_all_retreated,
            )
        )
        if (
            (plan.player_all_retreated or plan.enemy_all_retreated)
            and self.cfg.retreat_action == "none"
        ):
            log(
                "A side retreated completely. retreat_action=none: no damage was "
                "applied; the vanilla naval battle may need a manual retreat to end."
            )

        # Run the loss/damage batch on its own. If the later event batch has a
        # localization/encoding problem, the ships must still be destroyed and
        # the vanilla battle must still be zeroed out.
        try:
            combat_commands = hoi4_commands.writeback_commands(plan)
            log("Writing losses/damage to HOI4:")
            for cmd in combat_commands:
                log("   " + cmd)
            self.injector.inject(combat_commands)
        except InjectionError as exc:
            log(f"Writeback failed: {exc}")
            error_box(
                "The battle finished, but the losses could not be injected into HOI4.\n"
                "Use the in-game Abandon/Cancel decision if tactical mode is stuck.\n\n"
                + str(exc)
            )
            self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return
        except Exception as exc:
            log(f"Writeback error: {exc}")
            traceback.print_exc()
            error_box("Writeback error:\n" + str(exc))
            self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return

        try:
            vars_ = battle_mod.event_variables(result, plan)
            event_commands = hoi4_commands.result_event_commands(
                plan.player_tag, vars_["ntc_outcome"], vars_["ntc_summary"]
            )
            log("Writing result popup to HOI4:")
            for cmd in event_commands:
                log("   " + cmd)
            self.injector.inject(event_commands)
        except InjectionError as exc:
            log(f"Result popup injection failed (losses were applied): {exc}")
            error_box(
                "Losses were applied, but the result popup could not be injected.\n"
                "Use the in-game Abandon/Cancel decision to clear tactical mode.\n\n"
                + str(exc)
            )
            self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return
        except Exception as exc:
            log(f"Result popup error (losses were applied): {exc}")
            traceback.print_exc()
            self._abort_game_side(cand.player_tag)
            self._reset_battle()
            return

        log("Result written back.")
        log("   " + plan.summary)
        message_box(
            "Naval battle finished. The result was written back to HOI4.\n\n"
            + plan.summary
            + "\n\nYou can close navy-out and return to the strategic map.",
            "navy-command - battle finished",
        )
        self._reset_battle()

    def _battle_tick(self) -> bool:
        for event in self.tail.poll():
            if event.get("type") == "ntc_abort":
                self.abort_requested = True
                if self.session and self.session.process is not None:
                    navy_out.terminate(self.session.process)
                return False
        return True

    def _reset_battle(self) -> None:
        self.state = "idle"
        self.session = None
        self.pending = []
        self.pending_save = None
        self.pending_tag = ""
        self.abort_requested = False
        log("Idle again; waiting for the next scan.")

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------
    def handle_event(self, event: Dict) -> None:
        etype = event.get("type")
        if etype == "ntc_scan":
            if self.state != "idle":
                log(f"Ignoring scan request in state {self.state}.")
                return
            self.run_scan(str(event.get("tag", "")))
        elif etype == "ntc_pick":
            if self.state != "awaiting_pick":
                return
            slot = int(event.get("slot", 0))
            tag = str(event.get("tag", self.pending_tag))
            log(f"Player picked battle slot {slot}.")
            if not (1 <= slot <= len(self.pending)):
                log("Empty slot; treating it as cancel.")
                try:
                    self.injector.inject(
                        [f"eval_effect {tag} = {{ clr_country_flag = ntc_mode_active }}"]
                    )
                except InjectionError as exc:
                    log(f"Could not clear mode flag: {exc}")
                self._reset_battle()
                return
            self._start_battle(self.pending[slot - 1])
        elif etype == "ntc_cancel":
            if self.state == "awaiting_pick":
                log("Player cancelled the picker.")
                self._reset_battle()
        elif etype == "ntc_abort":
            if self.state == "in_battle":
                self.abort_requested = True
            else:
                log("HOI4 reports tactical mode is off.")
        elif etype == "ntc_complete":
            log("HOI4 reports battle complete.")
        elif etype == "ntc_quiet":
            log("HOI4 reports no battle found.")
        elif etype == "ntc_timeout":
            log("HOI4 reports the bridge did not answer in 12 hours.")
        elif etype == "ntc_scan_open":
            log("In-game picker opened.")

    def loop(self) -> None:
        log("Waiting for HOI4 signals (click Scan Naval Battles). Ctrl+C to exit.")
        while True:
            for event in self.tail.poll():
                try:
                    self.handle_event(event)
                except Exception as exc:
                    log(f"Handling event {event} failed: {exc}")
                    traceback.print_exc()
            if self.state == "awaiting_pick":
                waited = time.time() - self.awaiting_since
                if waited > 30 and time.time() - self._last_wait_warning > 60:
                    self._last_wait_warning = time.time()
                    log("Still waiting for the picker. If it did not open, check the mod or use Abandon/Cancel.")
            time.sleep(0.3)


# ---------------------------------------------------------------------------
# Offline diagnostics
# ---------------------------------------------------------------------------

def test_save(cfg: Config, path: str, build_index: int = 0) -> None:
    log("Offline parse mode (does not start HOI4 or navy-out)")
    mapdata = load_map(cfg.hoi4_game_dir, cfg.map_cache)
    groups = ship_names.load_name_groups(cfg.hoi4_game_dir)
    designer = DesignerDB(cfg.hoi4_game_dir)
    save = hoi4_save.load_save(path)
    resolved = ship_names.resolve_ship_names(save, groups)
    print(hoi4_save.save_summary(save))
    print("resolved names:", resolved)
    print(
        "designer:",
        len(designer.ship_hulls), "hulls",
        len(designer.ship_modules), "ship modules",
        len(designer.ship_upgrades), "ship upgrades",
        len(designer.plane_airframes), "airframes",
        len(designer.plane_modules), "plane modules",
        len(designer.plane_upgrades), "plane upgrades",
    )
    candidates = battle_mod.find_candidates(save, mapdata, max_battles=10)
    print(f"candidates: {len(candidates)}")
    for idx, cand in enumerate(candidates, start=1):
        print(f"{idx}. {cand.summary} province={cand.province} preset={cand.preset} active={cand.active}")
        print("   player:", ", ".join(f"{s.name}/{s.definition}/strength {s.strength:.0f}" for s in cand.player_ships[:8]))
        print("   enemy :", ", ".join(f"{s.name}/{s.definition}/strength {s.strength:.0f}" for s in cand.enemy_ships[:8]))
    if candidates and build_index > 0:
        idx = build_index - 1
        if not (0 <= idx < len(candidates)):
            print("build index out of range")
            return
        context, setup = battle_mod.build_context(
            candidates[idx], save, mapdata, cfg.session_dir, designer,
            with_carrier_air=cfg.carrier_air_wings,
        )
        setup_path = os.path.join(cfg.session_dir, "external_setup.json")
        navy_out.write_setup(setup, setup_path)
        battle_mod.dump_context(context, os.path.join(cfg.session_dir, "context.json"))
        print("wrote:", setup_path)
        print(json_dumps(setup)[:3000])


def json_dumps(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="HOI4 navy-command bridge")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dialog", action="store_true")
    parser.add_argument("--event", action="store_true")
    parser.add_argument("--from-start", action="store_true")
    parser.add_argument("--test-save", default=None)
    parser.add_argument("--build-setup", type=int, default=0)
    args = parser.parse_args()

    print(BANNER)
    cfg = load_config(args.config)
    if args.dry_run:
        cfg.inject_dry_run = True
    if args.dialog:
        cfg.selection = "dialog"
    if args.event:
        cfg.selection = "event"
    if args.from_start:
        cfg.log_from_start = True

    warnings = validate(cfg)
    for warning in warnings:
        log("WARNING: " + warning)

    if args.test_save:
        test_save(cfg, args.test_save, args.build_setup)
        return

    bridge = Bridge(cfg)
    bridge.load_world()
    bridge.loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExited.")
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        message_box("The bridge crashed. See the console for details.", "navy-command error")
        sys.exit(1)
