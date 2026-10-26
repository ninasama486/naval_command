"""Launch HOI4 directly with the local Naval Tactical Command mod enabled.

Some launchers (including third-party wrappers) ignore the Paradox playset DB
and launch the game with zero active mods. This helper writes the local mod
into dlc_load.json and starts hoi4.exe directly. It preserves all other active
mods except the Steam Workshop duplicate of this mod (same files), which would
otherwise conflict by name.

Usage:
  python launch_game.py            # set mod + launch
  python launch_game.py --dry-run  # show what would be enabled
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config, overrides_file_missing


LOCAL_MOD = "mod/naval-tactical-command.mod"
LOCAL_DIR_NAME = "naval-tactical-command"
# Default ASCII mod source tree. config.json can override it with
# mod_source_dir if the bridge and the mod live on different drives.
SOURCE_MOD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    LOCAL_DIR_NAME,
)
# Known published copies of this mod (they contain the same ntc_* files).
WORKSHOP_MODS = {
    "mod/ugc_3820620690.mod",
    "mod/ugc_3826829299.mod",
    "mod/ugc_3826841283.mod",
}


def _read_mod_path(user_dir: str, registry_id: str) -> str:
    path = os.path.join(user_dir, registry_id.replace("/", os.sep))
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    m = re.search(r'(?m)^\s*path\s*=\s*"([^"]+)"', text)
    if not m:
        return ""
    return m.group(1).replace("/", os.sep)


def _is_duplicate_naval_mod(user_dir: str, registry_id: str) -> bool:
    """True for any enabled mod that ships our ntc_decisions.txt.

    A second copy with the same internal IDs (the Workshop '海军前线指挥'
    upload is one) makes HOI4 drop or shadow the decision database.
    """
    if registry_id == LOCAL_MOD:
        return False
    if registry_id in WORKSHOP_MODS:
        return True
    mod_path = _read_mod_path(user_dir, registry_id)
    if not mod_path:
        return False
    return os.path.exists(
        os.path.join(mod_path, "common", "decisions", "ntc_decisions.txt")
    )


def sanitize_local_mod(
    user_dir: str,
    *,
    mod_source_dir: str = "",
    dry_run: bool = False,
) -> None:
    """Force the installed copy back to a *local* mod.

    Publishing from the launcher adds `remote_file_id=...` to the .mod file and
    descriptor; HOI4 then treats it as a Workshop item and, on this setup,
    skips it entirely (Active Mod Count: 0). Removing the field and ensuring a
    local `path=` keeps it loadable as a normal local mod.

    The user directory can be a Chinese OneDrive path, but HOI4/PhysFS cannot
    reliably mount mod files from it. The .mod file stays there; path= points
    at the ASCII mod source directory instead.
    """
    mod_file = os.path.join(user_dir, "mod", LOCAL_MOD.split("/", 1)[1])
    mod_dir = os.path.join(user_dir, "mod", LOCAL_DIR_NAME)
    candidates = (mod_source_dir, SOURCE_MOD_DIR, mod_dir)
    source_dir = next((p for p in candidates if p and os.path.isdir(p)), "")
    if not source_dir:
        return
    local_path = source_dir.replace("\\", "/")

    # A portable player may have the mod source but no installed .mod
    # descriptor yet. Create it so launching the game can work without a
    # separate install step.
    if not os.path.exists(mod_file) and not dry_run:
        os.makedirs(os.path.dirname(mod_file), exist_ok=True)
        descriptor = (
            'version="0.1.0"\r\n'
            'tags={\r\n'
            '\t"Gameplay"\r\n'
            '\t"Military"\r\n'
            '}\r\n'
            'name="Naval Tactical Command (Local)"\r\n'
            'supported_version="1.19.*"\r\n'
            f'path="{local_path}"\r\n'
        )
        with open(mod_file, "w", encoding="utf-8", newline="") as fh:
            fh.write(descriptor)

    def clean(path: str, ensure_path: bool) -> None:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline=None) as fh:
            # Blank lines are not meaningful in a .mod file; dropping them
            # also removes artifacts from earlier CR/CRLF conversions.
            lines = [line for line in fh.read().splitlines() if line.strip()]
        out = []
        has_path = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("remote_file_id="):
                continue
            if stripped.startswith("path="):
                out.append(f'path="{local_path}"')
                has_path = True
                continue
            out.append(line)
        if ensure_path and not has_path:
            out.append(f'path="{local_path}"')
        text = "\r\n".join(out) + "\r\n"
        if not dry_run:
            # newline="" disables Python's translation; joining with CRLF above
            # must not become \r\r\n (which HOI4 fails to parse).
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)

    clean(mod_file, ensure_path=True)
    clean(os.path.join(mod_dir, "descriptor.mod"), ensure_path=False)


def update_mods(user_dir: str, *, dry_run: bool = False) -> list[str]:
    path = os.path.join(user_dir, "dlc_load.json")
    data: dict = {"enabled_mods": [], "disabled_dlcs": []}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            try:
                data = json.load(fh)
            except ValueError:
                pass
    mods = list(data.get("enabled_mods", []))
    # Drop every copy that ships the same ntc_* files; use the local folder.
    mods = [
        m for m in mods
        if m != LOCAL_MOD and not _is_duplicate_naval_mod(user_dir, m)
    ]
    mods.append(LOCAL_MOD)
    data["enabled_mods"] = mods
    data.setdefault("disabled_dlcs", [])
    if not dry_run:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return mods


def game_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq hoi4.exe"],
            text=True,
            encoding="mbcs",
            errors="replace",
        )
        return "hoi4.exe" in out.lower()
    except Exception:
        pass
    try:
        import ctypes

        return bool(ctypes.windll.user32.FindWindowW(None, "Hearts of Iron IV (DirectX 11)"))
    except Exception:
        return False


def main() -> int:
    dry = "--dry-run" in sys.argv
    repair = "--repair" in sys.argv
    cfg = load_config()
    if not cfg.hoi4_user_dir or not os.path.isdir(cfg.hoi4_user_dir):
        print("找不到 HOI4 用户目录，请先检查项目根目录 settings.json。")
        return 1
    exe = os.path.join(cfg.hoi4_game_dir, "hoi4.exe")
    if not os.path.exists(exe):
        print(f"找不到 hoi4.exe：{exe}")
        return 1
    if not cfg.mod_source_dir and overrides_file_missing(cfg):
        print(
            "没有找到 mod 源目录，也没有已安装的本地 mod。\n"
            "请在 settings.json 的 mod_source_dir 填写 naval-tactical-command 文件夹，"
            "或运行 安装MOD.bat。"
        )
        return 1

    if not dry and not repair and game_running():
        print("HOI4 已经在运行。请先退出游戏，再运行本脚本。")
        return 2

    sanitize_local_mod(
        cfg.hoi4_user_dir, mod_source_dir=cfg.mod_source_dir, dry_run=dry
    )
    mods = update_mods(cfg.hoi4_user_dir, dry_run=dry)
    print("dlc_load.json 将启用的 mod：")
    for m in mods:
        print("  -", m)

    if repair:
        print("已把本地 .mod 修复为纯本地条目（去掉 remote_file_id），并写入 dlc_load.json。")
        print("请退出当前游戏，再双击“启动游戏-本地mod.bat”重新启动。")
        return 0
    if dry:
        print("（dry-run，没有写入，也没有启动游戏）")
        return 0

    print(f"启动：{exe}")
    subprocess.Popen([exe], cwd=cfg.hoi4_game_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
