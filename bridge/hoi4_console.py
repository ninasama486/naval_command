"""Win32 console injection into the running HOI4 process.

The approach is the one proven by Forward Command (GPL-3.0):
  * find the HOI4 window by title prefix
  * post `~` to open the Clausewitz console
  * post `run navy_command_inject.txt` + Enter (WM_CHAR)
  * verify execution with a marker line written to game.log

The console `run` command resolves paths relative to the HOI4 user directory,
so the batch file is written there under a fixed name.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time
from typing import Iterable, List, Optional, Sequence

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_OEM_3 = 0xC0
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
MAPVK_VK_TO_VSC = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
SW_RESTORE = 9

BATCH_NAME = "navy_command_inject.txt"
WINDOW_TITLE_PREFIX = "Hearts of Iron IV"


class InjectionError(RuntimeError):
    pass


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class _INPUTUNION(ctypes.Union):
    # MOUSEINPUT is the largest member of the real INPUT union (32 bytes on
    # x64); padding keeps sizeof(INPUT) at 40 bytes for SendInput.
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("padding", ctypes.c_byte * 32),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wt.DWORD),
        ("u", _INPUTUNION),
    ]


class Hoi4Injector:
    def __init__(self, user_dir: str, game_log: str, *, dry_run: bool = False) -> None:
        self.user_dir = user_dir
        self.game_log = game_log
        self.dry_run = dry_run
        self.batch_path = os.path.join(user_dir, BATCH_NAME)
        self._hwnd = 0
        if os.name == "nt":
            self.user32 = ctypes.windll.user32
        else:
            self.user32 = None

    # ------------------------------------------------------------------
    # Window / input
    # ------------------------------------------------------------------
    def find_window(self) -> int:
        if self.dry_run or self.user32 is None:
            return 0
        found: List[tuple[int, int]] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def enum_proc(hwnd, _lparam):
            length = self.user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if not (title.startswith(WINDOW_TITLE_PREFIX) and self.user32.IsWindowVisible(hwnd)):
                return True
            rect = wt.RECT()
            self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            score = area
            # The actual game window carries the renderer suffix; the launcher
            # may share the title prefix. Prefer the renderer window.
            if any(tag in title for tag in ("DirectX", "Vulkan", "OpenGL")):
                score += 10_000_000
            if not self.user32.IsIconic(hwnd):
                score += 1_000_000
            found.append((score, hwnd))
            return True

        self.user32.EnumWindows(enum_proc, 0)
        if found:
            found.sort(reverse=True)
            self._hwnd = found[0][1]
        else:
            self._hwnd = 0
        return self._hwnd

    def _scan_code(self, vk: int) -> int:
        return int(self.user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFF

    def _post_key(self, vk: int) -> None:
        sc = self._scan_code(vk)
        down = 1 | (sc << 16)
        up = 0xC0000001 | (sc << 16)
        self.user32.PostMessageW(self._hwnd, WM_KEYDOWN, vk, down)
        time.sleep(0.03)
        self.user32.PostMessageW(self._hwnd, WM_KEYUP, vk, up)
        time.sleep(0.03)

    def _post_char(self, ch: str) -> None:
        code = ord(ch)
        if code > 0xFFFF:
            return
        self.user32.PostMessageW(self._hwnd, WM_CHAR, code, 1)
        time.sleep(0.012)

    def _type(self, text: str) -> None:
        for ch in text:
            self._post_char(ch)

    def _toggle_console(self) -> None:
        self._post_key(VK_OEM_3)

    # -- foreground fallback (SendInput) --------------------------------
    def _send_key_global(self, vk: int) -> None:
        scan = self._scan_code(vk)
        self.user32.keybd_event(vk, scan, 0, 0)
        time.sleep(0.03)
        self.user32.keybd_event(vk, scan, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)

    def _send_unicode_global(self, ch: str) -> None:
        code = ord(ch)
        if code > 0xFFFF:
            return
        down = _INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUTUNION(
                ki=_KEYBDINPUT(
                    wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE,
                    time=0, dwExtraInfo=None,
                )
            ),
        )
        up = _INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUTUNION(
                ki=_KEYBDINPUT(
                    wVk=0, wScan=code,
                    dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time=0, dwExtraInfo=None,
                )
            ),
        )
        self.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
        time.sleep(0.008)
        self.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))
        time.sleep(0.008)

    def _force_foreground(self) -> bool:
        """SetForegroundWindow often fails due to the Windows foreground lock.
        Attaching to the current foreground thread's input queue lifts it."""
        user32 = self.user32
        try:
            user32.ShowWindow(self._hwnd, SW_RESTORE)
            fg = user32.GetForegroundWindow()
            if fg == self._hwnd:
                return True
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            target_tid = user32.GetWindowThreadProcessId(self._hwnd, None)
            attached = False
            if fg_tid and target_tid and fg_tid != target_tid:
                attached = bool(user32.AttachThreadInput(fg_tid, target_tid, True))
            user32.BringWindowToTop(self._hwnd)
            user32.SetForegroundWindow(self._hwnd)
            user32.SetFocus(self._hwnd)
            if attached:
                user32.AttachThreadInput(fg_tid, target_tid, False)
            time.sleep(0.25)
            return user32.GetForegroundWindow() == self._hwnd
        except Exception:
            return False

    def _send_text_global_vk(self, text: str) -> None:
        """Type ASCII with real virtual-key events (works with raw input)."""
        VK_SHIFT = 0x10
        for ch in text:
            result = self.user32.VkKeyScanW(ord(ch))
            if result == -1:
                self._send_unicode_global(ch)
                continue
            vk = result & 0xFF
            shift = bool(result & 0x100)
            if shift:
                self.user32.keybd_event(VK_SHIFT, 0, 0, 0)
                time.sleep(0.01)
            self._send_key_global(vk)
            if shift:
                self.user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.01)

    def _foreground_attempt(self, commands: Sequence[str], marker: str) -> bool:
        """Real keyboard input for environments where PostMessage is ignored."""
        if self._hwnd == 0:
            self.find_window()
        if self._hwnd == 0:
            return False
        self._write_batch(commands)
        if not self._force_foreground():
            # Still try: some systems accept the input even if the foreground
            # check is unreliable.
            pass
        time.sleep(0.4)
        self._send_key_global(VK_OEM_3)
        time.sleep(0.5)
        self._send_text_global_vk(f"run {BATCH_NAME}")
        self._send_key_global(VK_RETURN)
        time.sleep(0.6)
        ok = self._wait_marker(marker, timeout=4.0)
        if ok:
            self._send_key_global(VK_OEM_3)
            time.sleep(0.2)
        return ok

    # ------------------------------------------------------------------
    # Batch / verification
    # ------------------------------------------------------------------
    def _write_batch(self, commands: Sequence[str]) -> None:
        os.makedirs(self.user_dir, exist_ok=True)
        # HOI4's `run` parser drops the final unterminated line.
        with open(self.batch_path, "w", encoding="utf-8", newline="\n") as fh:
            for cmd in commands:
                fh.write(cmd.rstrip("\r\n") + "\n")

    def _read_log_tail(self, max_bytes: int = 2_000_000) -> str:
        try:
            size = os.path.getsize(self.game_log)
            with open(self.game_log, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(max(0, size - max_bytes))
                return fh.read()
        except OSError:
            return ""

    def _wait_marker(self, marker: str, timeout: float = 4.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if marker in self._read_log_tail():
                return True
            time.sleep(0.15)
        return False

    def _run_batch_once(self, commands: Sequence[str], marker: str) -> bool:
        self._write_batch(commands)
        if self.dry_run:
            return True
        if self._hwnd == 0:
            self.find_window()
        if self._hwnd == 0:
            raise InjectionError(
                "找不到 Hearts of Iron IV 窗口。请先启动游戏并进入存档。"
            )

        self._toggle_console()
        time.sleep(0.35)
        self._type(f"run {BATCH_NAME}")
        self._post_key(VK_RETURN)
        time.sleep(0.45)
        ok = self._wait_marker(marker, timeout=4.0)
        if ok:
            self._toggle_console()
            time.sleep(0.2)
        # On failure the console is deliberately left open; inject() closes it
        # with Escape before the single retry.
        return ok

    def inject(self, commands: Sequence[str], marker: Optional[str] = None) -> bool:
        """Execute commands through the console. Returns True when the marker
        appeared in game.log (or immediately in dry-run mode)."""
        if marker is None:
            marker = f"NC_PING_{os.getpid()}_{int(time.time() * 1000)}"
        full = [f'eval_effect log = "{marker}"'] + list(commands)

        if self.dry_run:
            self._write_batch(full)
            print(f"[inject] dry-run batch written to {self.batch_path}")
            for cmd in full:
                print("   ", cmd)
            return True

        if self._run_batch_once(full, marker):
            return True

        # The toggle is stateless: the first attempt may have closed an
        # already-open console. Escape closes the console deterministically,
        # then the second attempt opens it fresh.
        time.sleep(0.15)
        self._post_key(VK_ESCAPE)
        time.sleep(0.2)
        if self._run_batch_once(full, marker):
            return True
        self._post_key(VK_ESCAPE)
        # Last resort: activate the window and use real keyboard input. This
        # steals focus for a second, but it is the only channel left when the
        # game ignores posted messages (some Windows/IME combinations).
        if self._foreground_attempt(full, marker):
            return True
        self._send_key_global(VK_ESCAPE)
        raise InjectionError(
            "控制台注入失败：命令没有在 game.log 中出现。请确认游戏窗口未最小化，"
            "并且控制台没有被其他程序占用。"
        )

    def inject_save(self, save_name: str) -> str:
        """Ask HOI4 to save, then return the path of the freshest matching save."""
        start = time.time()
        self.inject(["gamespeed 0", f"savegame {save_name}"])
        deadline = time.time() + 35.0
        best: Optional[str] = None
        while time.time() < deadline:
            for candidate in (
                os.path.join(self.user_dir, "save games", f"{save_name}.hoi4"),
                os.path.join(self.user_dir, "save games", f"{save_name}_temp.hoi4"),
            ):
                if os.path.exists(candidate) and os.path.getmtime(candidate) >= start - 2:
                    best = candidate
                    break
            if best:
                # Wait for the writer to settle (two stable size readings).
                s1 = os.path.getsize(best)
                time.sleep(0.4)
                s2 = os.path.getsize(best)
                if s1 == s2 and s2 > 0:
                    return best
            time.sleep(0.4)
        if best:
            return best
        raise InjectionError(
            f"等待存档写入超时：没有找到 {save_name}.hoi4。"
            "请确认存档目录可写，且游戏不是铁人模式。"
        )
