"""Incremental reader for HOI4's game.log signal channel."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Iterator, List, Optional


JSON_START = re.compile(r'\{\s*"type"\s*:\s*"ntc_')


def extract_json(line: str) -> Optional[Dict]:
    m = JSON_START.search(line)
    if not m:
        return None
    start = m.start()
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(line)):
        ch = line[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    raw = line[start:i + 1]
                    try:
                        return json.loads(raw)
                    except ValueError:
                        try:
                            return json.loads(raw.replace('\\"', '"'))
                        except ValueError:
                            return None
    return None


class LogTail:
    def __init__(self, path: str, *, from_start: bool = False) -> None:
        self.path = path
        self.offset = 0
        self._buffer = ""
        self._inode = None
        if os.path.exists(path) and not from_start:
            try:
                self.offset = os.path.getsize(path)
            except OSError:
                self.offset = 0
        self._remember_inode()

    def _remember_inode(self) -> None:
        try:
            st = os.stat(self.path)
            self._inode = (st.st_dev, st.st_ino)
        except OSError:
            self._inode = None

    def poll(self) -> List[Dict]:
        events: List[Dict] = []
        if not os.path.exists(self.path):
            return events
        try:
            st = os.stat(self.path)
        except OSError:
            return events
        if self._inode is not None and (st.st_dev, st.st_ino) != self._inode:
            # Rotated / recreated.
            self.offset = 0
            self._buffer = ""
            self._remember_inode()
        if st.st_size < self.offset:
            self.offset = 0
            self._buffer = ""
        if st.st_size == self.offset:
            return events
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
                self.offset = fh.tell()
        except OSError:
            return events
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            obj = extract_json(line)
            if obj is not None:
                events.append(obj)
        if len(self._buffer) > 200_000:
            # A malformed/very long line: drop it rather than grow forever.
            obj = extract_json(self._buffer)
            if obj is not None:
                events.append(obj)
            self._buffer = ""
        return events


def wait_for_event(
    tail: LogTail,
    predicate,
    timeout: float,
    *,
    poll_interval: float = 0.35,
    on_tick=None,
) -> Optional[Dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in tail.poll():
            if predicate(event):
                return event
        if on_tick is not None:
            if on_tick() is False:
                return None
        time.sleep(poll_interval)
    return None
