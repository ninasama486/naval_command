"""Validate settings.json and all paths required by the bridge.

Run this after editing settings.json, or from the root 设置路径.bat.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (  # noqa: E402
    SETTINGS_FILE,
    _installed_mod_path,
    load_config,
    overrides_file_missing,
    validate,
)


def _yes_no(value: bool) -> str:
    return "OK" if value else "缺失"


def _line(label: str, ok: bool, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"  [{_yes_no(ok)}] {label}{suffix}")


def main() -> int:
    print("=" * 64)
    print("navy-command 设置检查")
    print("=" * 64)
    print(f"设置文件: {SETTINGS_FILE}")
    print(f"文件状态: {'存在' if os.path.exists(SETTINGS_FILE) else '不存在（将使用 bridge/config.json + 自动检测）'}")

    try:
        cfg = load_config()
    except SystemExit as exc:
        print("\n读取配置失败：")
        print(exc)
        return 1

    print("\n解析出的路径：")
    print(f"  HOI4 安装目录 : {cfg.hoi4_game_dir or '（未找到）'}")
    print(f"  HOI4 用户目录 : {cfg.hoi4_user_dir or '（未找到）'}")
    print(f"  mod 源目录    : {cfg.mod_source_dir or '（未找到）'}")
    print(f"  navy-out      : {cfg.navy_out_exe or '（未找到）'}")
    print(f"  navy-out 目录 : {cfg.navy_out_workdir or '（未找到）'}")

    print("\n必需文件：")
    game_ok = bool(cfg.hoi4_game_dir) and os.path.exists(
        os.path.join(cfg.hoi4_game_dir, "hoi4.exe")
    )
    user_ok = bool(cfg.hoi4_user_dir) and os.path.isdir(cfg.hoi4_user_dir)
    saves_ok = bool(cfg.hoi4_user_dir) and os.path.isdir(
        os.path.join(cfg.hoi4_user_dir, "save games")
    )
    logs_ok = bool(cfg.hoi4_user_dir) and os.path.isdir(
        os.path.join(cfg.hoi4_user_dir, "logs")
    )
    mod_ok = bool(cfg.mod_source_dir) and os.path.exists(
        os.path.join(cfg.mod_source_dir, "common", "decisions", "ntc_decisions.txt")
    )
    navy_ok = bool(cfg.navy_out_exe) and os.path.exists(cfg.navy_out_exe)
    pck_ok = bool(cfg.navy_out_workdir) and os.path.exists(
        os.path.join(cfg.navy_out_workdir, "navy.pck")
    )
    installed_ok = not overrides_file_missing(cfg)
    installed_path = (
        _installed_mod_path(cfg.hoi4_user_dir) if cfg.hoi4_user_dir else ""
    )
    ascii_ok = bool(cfg.mod_source_dir) and cfg.mod_source_dir.isascii()

    _line("HOI4 安装目录 / hoi4.exe", game_ok, cfg.hoi4_game_dir)
    _line("HOI4 用户目录", user_ok, cfg.hoi4_user_dir)
    _line("save games 文件夹", saves_ok)
    _line("logs 文件夹", logs_ok)
    _line("mod 源目录 / ntc_decisions.txt", mod_ok, cfg.mod_source_dir)
    _line("mod 路径为 ASCII（PhysFS 要求）", ascii_ok)
    _line("已安装的本地 mod", installed_ok, installed_path)
    _line("navy-out 可执行文件", navy_ok, cfg.navy_out_exe)
    _line("navy-out / navy.pck", pck_ok, cfg.navy_out_workdir)

    warnings = []
    try:
        warnings = validate(cfg)
    except SystemExit as exc:
        print("\n配置错误：")
        print(exc)
        return 1

    if warnings:
        print("\n提示：")
        for warning in warnings:
            print(f"  - {warning}")

    if sys.version_info < (3, 10):
        print(
            "\n提示：当前 Python 版本低于 3.10；建议安装 Python 3.10 或更新版本。"
        )

    problems = [
        not game_ok,
        not user_ok,
        not saves_ok,
        not logs_ok,
        not mod_ok,
        not ascii_ok,
        not installed_ok,
        not navy_ok,
        not pck_ok,
    ]
    if any(problems):
        print("\n结果：还有必要文件没有找到。")
        print("请编辑项目根目录的 settings.json，然后重新运行本检查。")
        print("如果只缺已安装的本地 mod，请运行 bridge\\install.bat。")
        return 1

    print("\n结果：路径检查通过。")
    print("下一步：运行 bridge\\run_bridge.bat，再用 启动游戏-本地mod.bat 启动游戏。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
