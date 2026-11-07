# -*- coding: utf-8 -*-
"""终端抽象：原始模式、备用屏、按键解码、尺寸。"""

from __future__ import annotations

import os
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass


@dataclass
class Key:
    name: str
    char: str = ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"Key({self.name!r}, {self.char!r})"


_SEQUENCES = {
    b"\x1b[A": "up",
    b"\x1b[B": "down",
    b"\x1b[C": "right",
    b"\x1b[D": "left",
    b"\x1bOA": "up",
    b"\x1bOB": "down",
    b"\x1bOC": "right",
    b"\x1bOD": "left",
    b"\x1b[H": "home",
    b"\x1b[F": "end",
    b"\x1bOH": "home",
    b"\x1bOF": "end",
    b"\x1b[1~": "home",
    b"\x1b[4~": "end",
    b"\x1b[2~": "insert",
    b"\x1b[3~": "delete",
    b"\x1b[5~": "pgup",
    b"\x1b[6~": "pgdn",
    b"\x1b[Z": "backtab",
    b"\x1bOP": "f1",
}


class Terminal:
    def __init__(self) -> None:
        self.stdin_fd = sys.stdin.fileno()
        self.is_tty = sys.stdin.isatty() and sys.stdout.isatty()
        self._old_attrs = None
        self._entered = False
        self._buffer = b""
        self._esc_at = 0.0

    # ------------------------------------------------------------------
    def enter(self) -> None:
        if not self.is_tty:
            return
        self._old_attrs = termios.tcgetattr(self.stdin_fd)
        tty.setraw(self.stdin_fd)
        self.write("\x1b[?1049h\x1b[?25l\x1b[H\x1b[2J")
        self._entered = True
        self._buffer = b""

    def exit(self) -> None:
        if not self.is_tty or not self._entered:
            return
        self.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        try:
            termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self._old_attrs)
        except termios.error:
            pass
        self._entered = False

    # ------------------------------------------------------------------
    @staticmethod
    def write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(self.stdin_fd)
            return max(20, size.columns), max(8, size.lines)
        except OSError:
            return 80, 24

    # ------------------------------------------------------------------
    def read_key(self, timeout: float = 0.05) -> Key | None:
        if not self.is_tty:
            return None
        deadline = time.monotonic() + timeout
        while True:
            key = self._parse()
            if key is not None:
                return key
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._flush_partial()
            try:
                readable, _, _ = select.select([self.stdin_fd], [], [], remaining)
            except InterruptedError:
                continue
            if not readable:
                return self._flush_partial()
            try:
                chunk = os.read(self.stdin_fd, 256)
            except OSError:
                return Key("ctrl_c")
            if not chunk:
                return Key("ctrl_c")
            self._buffer += chunk

    # ------------------------------------------------------------------
    def _flush_partial(self) -> Key | None:
        if self._buffer == b"\x1b":
            self._buffer = b""
            return Key("esc")
        return None

    def _parse(self) -> Key | None:
        buf = self._buffer
        if not buf:
            return None

        if buf[0] == 0x1B:
            for seq, name in _SEQUENCES.items():
                if buf.startswith(seq):
                    self._buffer = buf[len(seq) :]
                    return Key(name)
            if any(seq.startswith(buf) for seq in _SEQUENCES):
                # 可能是未收完的转义序列
                return None
            # 鼠标等未知 CSI：吞掉到字母结尾
            if buf.startswith(b"\x1b[") and len(buf) > 2:
                index = 2
                while index < len(buf) and not (0x40 <= buf[index] <= 0x7E):
                    index += 1
                if index < len(buf):
                    self._buffer = buf[index + 1 :]
                    return Key("unknown")
                return None
            self._buffer = buf[1:]
            return Key("esc")

        byte = buf[0]
        if byte in (0x0D, 0x0A):
            self._buffer = buf[1:]
            return Key("enter")
        if byte in (0x7F, 0x08):
            self._buffer = buf[1:]
            return Key("backspace")
        if byte == 0x09:
            self._buffer = buf[1:]
            return Key("tab")
        if byte == 0x03:
            self._buffer = buf[1:]
            return Key("ctrl_c")
        if byte == 0x04:
            self._buffer = buf[1:]
            return Key("ctrl_d")
        if byte == 0x15:
            self._buffer = buf[1:]
            return Key("ctrl_u")
        if byte == 0x17:
            self._buffer = buf[1:]
            return Key("ctrl_w")
        if byte == 0x0C:
            self._buffer = buf[1:]
            return Key("ctrl_l")
        if byte == 0x00:
            self._buffer = buf[1:]
            return Key("ctrl_space")
        if byte < 0x20:
            self._buffer = buf[1:]
            return Key("unknown")

        # UTF-8
        if byte < 0x80:
            length = 1
        elif 0xC2 <= byte <= 0xDF:
            length = 2
        elif 0xE0 <= byte <= 0xEF:
            length = 3
        elif 0xF0 <= byte <= 0xF4:
            length = 4
        else:
            self._buffer = buf[1:]
            return Key("unknown")
        if len(buf) < length:
            return None
        try:
            char = buf[:length].decode("utf-8")
        except UnicodeDecodeError:
            self._buffer = buf[1:]
            return Key("unknown")
        self._buffer = buf[length:]
        if char == " ":
            return Key("space", " ")
        return Key("char", char)
