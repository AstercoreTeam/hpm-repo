# -*- coding: utf-8 -*-
"""可复用界面控件：菜单、表格、页脚、弹窗。"""

from __future__ import annotations

from dataclasses import dataclass

from .screen import Screen, Style, ellipsize, st, text_width, wrap_text


@dataclass
class MenuItem:
    label: str
    hint: str = ""
    enabled: bool = True
    danger: bool = False
    action: str = ""


def menu_move(items: list[MenuItem], cursor: int, delta: int) -> int:
    if not items:
        return 0
    index = cursor
    for _ in range(len(items)):
        index = (index + delta) % len(items)
        if items[index].enabled:
            return index
    return cursor


def draw_menu(
    screen: Screen,
    x: int,
    y: int,
    width: int,
    items: list[MenuItem],
    cursor: int,
) -> int:
    for index, item in enumerate(items):
        selected = index == cursor
        if selected:
            style = st(fg="white", bg="select", bold=True)
        elif not item.enabled:
            style = st(fg="dim", dim=True)
        elif item.danger:
            style = st(fg="red")
        else:
            style = st(fg="text")
        screen.fill(x, y + index, width, 1, style)
        marker = "▸ " if selected else "  "
        label = ellipsize(item.label, max(1, width - 4))
        screen.put(x + 1, y + index, marker + label, style)
        if item.hint:
            hint_width = text_width(item.hint)
            hint_x = x + width - hint_width - 2
            if hint_x > x + text_width(marker + label) + 2:
                screen.put(hint_x, y + index, item.hint, style if selected else st(fg="muted"))
    return len(items)


def draw_title(screen: Screen, title: str, subtitle: str = "") -> None:
    width = screen.w
    screen.fill(0, 0, width, 1, st(fg="cyan", bg="header", bold=True))
    screen.put(2, 0, title, st(fg="cyan", bg="header", bold=True))
    if subtitle:
        right = width - text_width(subtitle) - 2
        if right > text_width(title) + 4:
            screen.put(right, 0, subtitle, st(fg="muted", bg="header"))


def draw_footer(screen: Screen, hints: str) -> None:
    y = screen.h - 1
    screen.fill(0, y, screen.w, 1, st(fg="muted", bg="footer"))
    screen.put(1, y, ellipsize(hints, screen.w - 2), st(fg="muted", bg="footer"))


def draw_toast(screen: Screen, message: str, level: str = "info") -> None:
    if not message:
        return
    color = {"info": "cyan", "ok": "green", "error": "red", "warn": "yellow"}.get(
        level, "cyan"
    )
    y = screen.h - 2
    screen.fill(0, y, screen.w, 1, st(fg=color, bg="panel"))
    screen.put(1, y, ellipsize(message, screen.w - 2), st(fg=color, bg="panel"))


def _pad(text: str, width: int) -> str:
    text = ellipsize(text, width)
    return text + " " * max(0, width - text_width(text))


def draw_table(
    screen: Screen,
    x: int,
    y: int,
    width: int,
    headers: list[str],
    rows: list[list[str]],
    widths: list[int],
    cursor: int = -1,
    offset: int = 0,
    max_rows: int | None = None,
) -> int:
    header_style = st(fg="yellow", bg="panel", bold=True)
    screen.fill(x, y, width, 1, header_style)
    col = x
    for index, header in enumerate(headers):
        screen.put(col, y, _pad(header, widths[index]), header_style)
        col += widths[index] + 1

    visible = rows[offset:]
    if max_rows is not None:
        visible = visible[:max_rows]
    for row_index, row in enumerate(visible):
        index = offset + row_index
        style = (
            st(fg="white", bg="select")
            if index == cursor
            else st(fg="text", bg="panel_alt")
        )
        screen.fill(x, y + 1 + row_index, width, 1, style)
        col = x
        for c_index, value in enumerate(row):
            if c_index >= len(widths):
                break
            cell_style = style
            if index != cursor and c_index == 0:
                cell_style = style
            screen.put(col, y + 1 + row_index, _pad(value, widths[c_index]), cell_style)
            col += widths[c_index] + 1
    return len(visible)


def modal_geometry(screen: Screen, width: int, height: int) -> tuple[int, int, int, int]:
    width = min(width, screen.w - 4)
    height = min(height, screen.h - 4)
    x = (screen.w - width) // 2
    y = (screen.h - height) // 2
    return x, y, width, height


def draw_modal(
    screen: Screen,
    title: str,
    lines: list[str],
    *,
    width: int = 62,
    options: list[str] | None = None,
    cursor: int = 0,
    hint: str = "",
) -> tuple[int, int, int, int]:
    inner = max(20, width - 6)
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(wrap_text(line, inner) if line else [""])
    height = len(wrapped) + 5 + (1 if options else 0)
    x, y, w, h = modal_geometry(screen, width, height)
    screen.fill(x + 1, y + 1, w - 2, h - 2, st(fg="text", bg="modal"))
    screen.box(
        x,
        y,
        w,
        h,
        title,
        st(fg="text", bg="modal"),
        st(fg="cyan", bg="modal", bold=True),
    )
    row = y + 2
    for line in wrapped:
        screen.put(x + 3, row, line, st(fg="text", bg="modal"))
        row += 1
    if options:
        row += 1
        col = x + 3
        for index, option in enumerate(options):
            selected = index == cursor
            style = (
                st(fg="white", bg="select", bold=True)
                if selected
                else st(fg="muted", bg="modal")
            )
            label = f" {option} "
            screen.put(col, row, label, style)
            col += text_width(label) + 2
    if hint:
        screen.put(
            x + 3,
            y + h - 2,
            ellipsize(hint, w - 6),
            st(fg="muted", bg="modal"),
        )
    return x, y, w, h
