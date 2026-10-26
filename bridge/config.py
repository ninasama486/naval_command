"""Configuration loading and path discovery."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import List, Optional


BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
NAVY_DIR = os.path.dirname(BRIDGE_DIR)
# Player-facing settings file at the project root. It overrides
# bridge/config.json and supports project-relative paths.
SETTINGS_FILE = os.path.join(NAVY_DIR, "settings.json")
DEFAULT_CONFIG_FILE = os.path.join(BRIDGE_DIR, "config.json")


@dataclass
class Config:
    hoi4_user_dir: str = ""
    hoi4_game_dir: str = ""
    # ASCII directory HOI4 mounts as the local mod. It can differ from the
    # (possibly Chinese) HOI4 user directory.
    mod_source_dir: str = ""
    navy_out_exe: str = ""
    navy_out_workdir: str = ""
    session_dir: str = ""
    cache_dir: str = ""
    selection: str = "event"  # event | dialog
    max_battles: int = 3
    carrier_air_wings: bool = True
    # When one or both sides retreat completely, HOI4 has no script effect to
    # end the naval battle without moving ships. "org" zeroes only the
    # retreating side's org (no HP) so it disengages; "none" leaves both sides
    # completely untouched and requires a manual retreat.
    retreat_action: str = "org"
    inject_dry_run: bool = False
    log_from_start: bool = False
    save_name: str = "navy_command_scan"
    extra: dict = field(default_factory=dict)

    # -- derived paths ----------------------------------------------------
    @property
    def game_log(self) -> str:
        return os.path.join(self.hoi4_user_dir, "logs", "game.log")

    @property
    def save_dir(self) -> str:
        return os.path.join(self.hoi4_user_dir, "save games")

    @property
    def settings_file(self) -> str:
        return os.path.join(self.hoi4_user_dir, "settings.txt")

    @property
    def map_cache(self) -> str:
        return os.path.join(self.cache_dir, "hoi4_map.json")


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path or "")).strip()


def _resolve_path(value: str) -> str:
    """Expand env vars and resolve relative paths against the project root."""
    text = _expand(str(value or ""))
    if not text:
        return ""
    if not os.path.isabs(text):
        text = os.path.join(NAVY_DIR, text)
    return os.path.normpath(text)


def _read_json(path: str, label: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"无法读取{label} {path}: {exc}\n"
            "请检查 JSON 格式：路径使用单引号/双引号、字段之间用逗号，不能写注释。"
        )
    if not isinstance(data, dict):
        raise SystemExit(f"{label}格式错误：{path} 的顶层必须是对象 {{ ... }}。")
    return data


def _merge_player_settings(base: dict, player: dict) -> dict:
    merged = dict(base)
    for key, value in player.items():
        if str(key).startswith("_"):
            continue
        merged[key] = value
    return merged


def _installed_mod_path(user_dir: str) -> str:
    """Read path= from the installed .mod file."""
    mod_file = os.path.join(user_dir, "mod", "naval-tactical-command.mod")
    if not os.path.exists(mod_file):
        return ""
    try:
        with open(mod_file, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    match = re.search(r'(?m)^\s*path\s*=\s*"([^"]+)"', text)
    if not match:
        return ""
    path = match.group(1).replace("/", os.sep)
    if not os.path.isabs(path):
        path = os.path.join(user_dir, path)
    return os.path.normpath(path)


def _detect_mod_source(user_dir: str) -> str:
    local = os.path.join(NAVY_DIR, "naval-tactical-command")
    if os.path.isdir(local):
        return local
    installed = _installed_mod_path(user_dir)
    if installed and os.path.isdir(installed):
        return installed
    installed_dir = os.path.join(user_dir, "mod", "naval-tactical-command")
    if os.path.isdir(installed_dir):
        return installed_dir
    return ""


def _user_dir_candidates() -> List[str]:
    home = os.path.expanduser("~")
    names = [
        os.path.join("OneDrive", "文档", "Paradox Interactive", "Hearts of Iron IV"),
        os.path.join("OneDrive", "Documents", "Paradox Interactive", "Hearts of Iron IV"),
        os.path.join("Documents", "Paradox Interactive", "Hearts of Iron IV"),
        os.path.join("文档", "Paradox Interactive", "Hearts of Iron IV"),
        os.path.join("OneDrive", "ドキュメント", "Paradox Interactive", "Hearts of Iron IV"),
    ]
    return [os.path.join(home, n) for n in names]


def _game_dir_candidates() -> List[str]:
    return [
        r"D:\game\steam\steamapps\common\Hearts of Iron IV",
        r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV",
        r"C:\Program Files\Steam\steamapps\common\Hearts of Iron IV",
        r"D:\SteamLibrary\steamapps\common\Hearts of Iron IV",
        r"E:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    ]


def _steam_library_game_candidates() -> List[str]:
    """Best-effort Steam library discovery for other people's machines."""
    roots = []
    for env_name in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        base = os.environ.get(env_name)
        if base:
            roots.append(os.path.join(base, "Steam"))
    roots.extend((r"C:\Steam", r"D:\Steam", r"E:\Steam"))
    libraries = []
    for root in roots:
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if not os.path.exists(vdf):
            continue
        try:
            with open(vdf, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            libraries.append(match.group(1).replace("\\\\", "\\"))
    return [
        os.path.join(lib, "steamapps", "common", "Hearts of Iron IV")
        for lib in libraries
    ]


def _detect_user_dir() -> str:
    for path in _user_dir_candidates():
        if os.path.isdir(os.path.join(path, "save games")) or os.path.isdir(
            os.path.join(path, "logs")
        ):
            return path
    return ""


def _detect_game_dir() -> str:
    for path in _game_dir_candidates() + _steam_library_game_candidates():
        if os.path.exists(os.path.join(path, "hoi4.exe")):
            return path
    return ""


def _detect_navy_out() -> str:
    for path in (
        os.path.join(NAVY_DIR, "navy-out", "navy.exe"),
        os.path.join(NAVY_DIR, "navy-out", "navy.console.exe"),
    ):
        if os.path.exists(path):
            return path
    return ""


def load_config(path: Optional[str] = None) -> Config:
    """Load bridge config, then overlay the player-facing settings.json.

    An explicit --config path is treated as a complete legacy config and is
    not overlaid with settings.json. An empty path value in settings.json
    intentionally means "auto-detect", overriding a machine-specific value in
    bridge/config.json.
    """
    if path is not None:
        raw = _read_json(path, "配置文件")
    else:
        raw = _merge_player_settings(
            _read_json(DEFAULT_CONFIG_FILE, "桥接配置文件"),
            _read_json(SETTINGS_FILE, "玩家设置文件"),
        )

    cfg = Config(
        hoi4_user_dir=_resolve_path(raw.get("hoi4_user_dir", "")),
        hoi4_game_dir=_resolve_path(raw.get("hoi4_game_dir", "")),
        mod_source_dir=_resolve_path(raw.get("mod_source_dir", "")),
        navy_out_exe=_resolve_path(raw.get("navy_out_exe", "")),
        navy_out_workdir=_resolve_path(raw.get("navy_out_workdir", "")),
        session_dir=_resolve_path(raw.get("session_dir", ""))
        or os.path.join(BRIDGE_DIR, "session"),
        cache_dir=_resolve_path(raw.get("cache_dir", ""))
        or os.path.join(BRIDGE_DIR, "cache"),
        selection=str(raw.get("selection", "event")).lower(),
        max_battles=max(1, min(3, int(raw.get("max_battles", 3)))),
        # carrier_air_group is the old name; keep it as a fallback.
        carrier_air_wings=bool(
            raw.get("carrier_air_wings", raw.get("carrier_air_group", True))
        ),
        retreat_action=str(raw.get("retreat_action", "org")).lower(),
        inject_dry_run=bool(raw.get("inject_dry_run", False)),
        log_from_start=bool(raw.get("log_from_start", False)),
        save_name=str(raw.get("save_name", "navy_command_scan")),
        extra=raw,
    )

    if not cfg.hoi4_user_dir:
        cfg.hoi4_user_dir = _detect_user_dir()
    if not cfg.hoi4_game_dir:
        cfg.hoi4_game_dir = _detect_game_dir()
    if not cfg.mod_source_dir:
        cfg.mod_source_dir = _detect_mod_source(cfg.hoi4_user_dir)
    if not cfg.navy_out_exe:
        cfg.navy_out_exe = _detect_navy_out()
    if not cfg.navy_out_workdir:
        cfg.navy_out_workdir = os.path.dirname(cfg.navy_out_exe)
    if cfg.selection not in ("event", "dialog"):
        cfg.selection = "event"
    if cfg.retreat_action not in ("none", "org"):
        cfg.retreat_action = "org"

    os.makedirs(cfg.session_dir, exist_ok=True)
    os.makedirs(cfg.cache_dir, exist_ok=True)
    return cfg


def validate(cfg: Config) -> List[str]:
    """Return non-fatal warnings. Fatal problems raise SystemExit."""
    errors: List[str] = []
    warnings: List[str] = []

    if not cfg.hoi4_user_dir or not os.path.isdir(cfg.hoi4_user_dir):
        errors.append(
            "找不到 HOI4 用户目录（包含 save games / logs）。"
            "请在项目根目录 settings.json 里填写 hoi4_user_dir。"
        )
    if not cfg.hoi4_game_dir or not os.path.isdir(cfg.hoi4_game_dir):
        errors.append(
            "找不到 HOI4 安装目录（包含 hoi4.exe）。"
            "请在项目根目录 settings.json 里填写 hoi4_game_dir。"
        )
    if not cfg.navy_out_exe or not os.path.exists(cfg.navy_out_exe):
        errors.append(
            "找不到 navy-out 可执行文件。"
            "请在 settings.json 里填写 navy_out_exe，或把 navy-out 放在项目目录下。"
        )
    if cfg.mod_source_dir:
        if not os.path.isdir(cfg.mod_source_dir):
            warnings.append(
                f"mod_source_dir 不存在：{cfg.mod_source_dir}；"
                "请在 settings.json 里改为实际的 mod 文件夹。"
            )
        elif not os.path.exists(
            os.path.join(cfg.mod_source_dir, "common", "decisions", "ntc_decisions.txt")
        ):
            warnings.append(
                f"mod_source_dir 里没有 common/decisions/ntc_decisions.txt："
                f"{cfg.mod_source_dir}"
            )
        if not cfg.mod_source_dir.isascii():
            warnings.append(
                "mod_source_dir 含非 ASCII 字符，HOI4/PhysFS 可能无法挂载；"
                "请把 mod 复制到纯英文路径后重新安装。"
            )
    else:
        warnings.append(
            "没有找到 mod 源目录。请在 settings.json 填写 mod_source_dir，"
            "或先安装 mod 后再运行桥接程序。"
        )
    if cfg.navy_out_workdir and not os.path.isdir(cfg.navy_out_workdir):
        warnings.append(f"navy_out_workdir 不存在：{cfg.navy_out_workdir}")
    elif cfg.navy_out_workdir and not os.path.exists(
        os.path.join(cfg.navy_out_workdir, "navy.pck")
    ):
        warnings.append(
            f"navy-out 目录里没有 navy.pck：{cfg.navy_out_workdir}；"
            "navy.exe 可能无法启动。"
        )
    if overrides_file_missing(cfg):
        warnings.append(
            "没有在 HOI4 用户目录找到已安装的海军战术指挥 mod。"
            "请检查 settings.json 的路径，然后运行 bridge\\install.bat。"
        )

    if errors:
        raise SystemExit("配置错误：\n  - " + "\n  - ".join(errors))

    settings = cfg.settings_file
    if os.path.exists(settings):
        try:
            with open(settings, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if "save_as_binary=yes" in text.replace(" ", ""):
                warnings.append(
                    "settings.txt 里 save_as_binary=yes。桥接程序只能解析文本存档，"
                    "请在游戏设置中关闭二进制存档。"
                )
        except OSError:
            pass
    else:
        warnings.append(f"没有找到 settings.txt：{settings}")

    if not os.path.exists(cfg.game_log):
        warnings.append(f"还没有 game.log：{cfg.game_log}（启动游戏后会生成）")

    return warnings


def overrides_file_missing(cfg: Config) -> bool:
    """True when there is no usable local .mod descriptor.

    The .mod can point at an ASCII source directory; the installed copy under
    the user directory is only a fallback, not a requirement.
    """
    mod_file = os.path.join(cfg.hoi4_user_dir, "mod", "naval-tactical-command.mod")
    if not os.path.exists(mod_file):
        return True
    path = _installed_mod_path(cfg.hoi4_user_dir)
    if path and os.path.isdir(path):
        return False
    fallback = os.path.join(
        cfg.hoi4_user_dir, "mod", "naval-tactical-command"
    )
    return not os.path.isdir(fallback)
