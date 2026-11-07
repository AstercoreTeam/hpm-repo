# -*- coding: utf-8 -*-
"""视图基类。"""

from __future__ import annotations

from ..tui.screen import Screen
from ..tui.widgets import MenuItem, draw_footer, draw_menu, draw_title, menu_move


class View:
    title = ""
    subtitle = ""

    def __init__(self, app) -> None:
        self.app = app

    def update(self, now: float) -> None:
        pass

    def render(self, screen: Screen) -> None:
        draw_title(screen, self.title, self.subtitle)

    def handle_key(self, key) -> None:
        pass

    def footer(self) -> str:
        return "Esc 返回 · ? 帮助"


class MenuView(View):
    footer_hint = "↑↓ 移动 · Enter 确认 · Esc 返回 · ? 帮助"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.cursor = 0
        self.items: list[MenuItem] = []

    def build_items(self) -> list[MenuItem]:
        return []

    def refresh_items(self) -> None:
        self.items = self.build_items()
        if self.items and not self.items[self.cursor].enabled:
            self.cursor = menu_move(self.items, self.cursor, 1)

    def on_select(self, item: MenuItem) -> None:
        pass

    def header_lines(self) -> list[str]:
        return []

    def render(self, screen: Screen) -> None:
        draw_title(screen, self.title, self.subtitle)
        y = 2
        lines = self.header_lines()
        if lines:
            from ..tui.screen import ellipsize, st

            width = screen.w - 6
            for line in lines:
                screen.put(3, y, ellipsize(line, width), st(fg="muted"))
                y += 1
            y += 1
        if not self.items:
            self.refresh_items()
        draw_menu(screen, 3, y, screen.w - 6, self.items, self.cursor)
        draw_footer(screen, self.footer_hint)

    def handle_key(self, key) -> None:
        if key.name in ("up", "k"):
            self.cursor = menu_move(self.items, self.cursor, -1)
        elif key.name in ("down", "j"):
            self.cursor = menu_move(self.items, self.cursor, 1)
        elif key.name == "enter":
            if not self.items:
                return
            item = self.items[self.cursor]
            if item.enabled:
                self.on_select(item)
        elif key.name == "esc" or (key.name == "char" and key.char == "q"):
            self.app.pop()
        elif key.name == "char" and key.char == "r":
            self.refresh_items()
        elif key.name == "char" and key.char == "?":
            from .help import HelpView

            self.app.push(HelpView(self.app))
