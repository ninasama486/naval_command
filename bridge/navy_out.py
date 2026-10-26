"""Launch navy-out with an external battle setup and read its result file."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Callable, Optional, Tuple

from result_parser import is_complete


class NavyOutError(RuntimeError):
    pass


def drop_stale_results(paths) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def default_user_result() -> str:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return ""
    return os.path.join(appdata, "Godot", "app_userdata", "navy", "battle_result.txt")


def write_setup(setup: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(setup, fh, ensure_ascii=False, indent=2)


def result_candidates(output_path: str, workdir: str) -> list[str]:
    paths = [
        output_path,
        default_user_result(),
        os.path.join(workdir, "battle_result.txt"),
    ]
    seen = set()
    out = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def launch_and_wait(
    exe: str,
    workdir: str,
    setup: dict,
    setup_path: str,
    output_path: str,
    *,
    timeout: float = 7200.0,
    on_tick: Optional[Callable[[], bool]] = None,
) -> Tuple[str, subprocess.Popen]:
    """Start navy-out, wait for a complete result text, return (text, process).

    `on_tick` is called about twice a second; returning False aborts the wait
    (used to react to HOI4's Force Exit decision).
    """
    if not os.path.exists(exe):
        raise NavyOutError(f"找不到 navy-out 可执行文件：{exe}")

    write_setup(setup, setup_path)
    candidates = result_candidates(output_path, workdir)
    drop_stale_results(candidates)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [exe, f"--external-setup={setup_path}"],
        cwd=workdir,
        creationflags=creationflags,
    )

    started = time.time()
    deadline = started + timeout
    last_error = ""
    while time.time() < deadline:
        for path in candidates:
            try:
                if not os.path.exists(path):
                    continue
                # Never accept a stale result that could not be deleted.
                if os.path.getmtime(path) < started - 2:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                last_error = str(exc)
                continue
            if is_complete(text):
                return text, proc
        if proc.poll() is not None:
            # Give the game one last chance to flush the result file.
            time.sleep(1.0)
            for path in candidates:
                try:
                    if os.path.exists(path) and os.path.getmtime(path) >= started - 2:
                        with open(path, "r", encoding="utf-8", errors="replace") as fh:
                            text = fh.read()
                        if is_complete(text):
                            return text, proc
                except OSError:
                    pass
            raise NavyOutError(
                "navy-out 已退出，但没有写出完整的战果文件。"
                f"（退出码 {proc.returncode}）"
            )
        if on_tick is not None and on_tick() is False:
            raise NavyOutError("战术指挥已放弃。")
        time.sleep(0.5)

    raise NavyOutError("等待 navy-out 战斗结果超时。")


def terminate(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
