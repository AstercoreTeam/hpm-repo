# -*- coding: utf-8 -*-
"""启动动画：hpm-repo 字符画从左到右显示。"""

from __future__ import annotations

import time

from .. import __app_long_name__, __version__
from ..tui.screen import Screen, st, text_width
from .base import View

_GLYPHS: dict[str, list[str]] = {
    "h": ["█  █", "█  █", "████", "█  █", "█  █"],
    "p": ["████", "█  █", "████", "█   ", "█   "],
    "m": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "-": ["    ", "    ", "████", "    ", "    "],
    "r": ["█ ██", "██  ", "█   ", "█   ", "█   "],
    "e": ["████", "█   ", "████", "█   ", "████"],
    "o": ["████", "█  █", "█  █", "█  █", "████"],
}
_TEXT = "hpm-repo"


def _banner() -> list[str]:
    rows = [""] * 5
    for index, char in enumerate(_TEXT):
        glyph = _GLYPHS[char]
        for row in range(5):
            rows[row] += glyph[row]
            if index < len(_TEXT) - 1:
                rows[row] += "  "
    return rows


BANNER = _banner()
BANNER_WIDTH = max(text_width(line) for line in BANNER)


class SplashView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.started = time.monotonic()
        self.finished = False
        self.reveal_seconds = 0.022
        self.hold_seconds = 0.45
        if not app.animate:
            self.started = 0.0

    def _elapsed(self, now: float) -> float:
        return now - self.started

    def update(self, now: float) -> None:
        if self.finished:
            return
        if not self.app.animate:
            if self._elapsed(now) > 0.25:
                self._finish()
            return
        total = BANNER_WIDTH * self.reveal_seconds
        if self._elapsed(now) > total + self.hold_seconds:
            self._finish()

    def _finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.app.home()

    def handle_key(self, key) -> None:
        if key.name == "ctrl_c":
            self.app.running = False
            return
        if not self.app.animate:
            self._finish()
            return
        # 任意键立即完成动画
        elapsed = time.monotonic() - self.started
        total = BANNER_WIDTH * self.reveal_seconds
        if elapsed < total:
            self.started = time.monotonic() - total
        else:
            self._finish()

    def render(self, screen: Screen) -> None:
        screen.clear()
        now = time.monotonic()
        if screen.w < BANNER_WIDTH + 4:
            text = "hpm-repo"
            x = max(0, (screen.w - len(text)) // 2)
            y = max(1, screen.h // 2 - 2)
            screen.put(x, y, text, st(fg="cyan", bold=True))
        else:
            if self.app.animate:
                elapsed = max(0.0, self._elapsed(now))
                reveal = int(elapsed / self.reveal_seconds)
            else:
                reveal = BANNER_WIDTH
            start_y = max(1, screen.h // 2 - 4)
            start_x = (screen.w - BANNER_WIDTH) // 2
            for row, line in enumerate(BANNER):
                visible = line[:reveal]
                screen.put(
                    start_x,
                    start_y + row,
                    visible,
                    st(fg="cyan", bold=True),
                )
            # 已显示部分的尾部高亮，模拟扫描线
            if reveal < BANNER_WIDTH:
                scan_x = start_x + reveal
                if scan_x < start_x + BANNER_WIDTH:
                    for row in range(5):
                        screen.put(scan_x, start_y + row, "░", st(fg="blue"))
            screen.put(
                start_x,
                start_y + 6,
                _center(f"{__app_long_name__}  v{__version__}", BANNER_WIDTH),
                st(fg="muted"),
            )

        hint = "按任意键跳过" if self.app.animate else ""
        if hint:
            screen.put(
                max(0, (screen.w - text_width(hint)) // 2),
                screen.h - 2,
                hint,
                st(fg="dim", dim=True),
            )


def _center(text: str, width: int) -> str:
    pad = max(0, (width - text_width(text)) // 2)
    return " " * pad + text
