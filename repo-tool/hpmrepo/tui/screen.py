# -*- coding: utf-8 -*-
"""双缓冲字符画布与 ANSI 输出。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from . import theme


@dataclass(frozen=True)
class Style:
    fg: int | None = None
    bg: int | None = None
    bold: bool = False
    dim: bool = False
    reverse: bool = False


DEFAULT = Style()


def st(
    fg: str | int | None = None,
    bg: str | int | None = None,
    bold: bool = False,
    dim: bool = False,
    reverse: bool = False,
) -> Style:
    def resolve(value: str | int | None, table: dict[str, int]) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        return table.get(value)

    if not theme.color_enabled():
        return Style(bold=bold)
    return Style(
        fg=resolve(fg, theme.FG),
        bg=resolve(bg, theme.BG),
        bold=bold,
        dim=dim,
        reverse=reverse,
    )


def char_width(ch: str) -> int:
    if not ch:
        return 0
    code = ord(ch)
    if code < 32 or code == 0x7F:
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def text_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def ellipsize(text: str, width: int) -> str:
    if text_width(text) <= width:
        return text
    if width <= 1:
        return "…" * max(0, width)
    out: list[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def wrap_text(text: str, width: int) -> list[str]:
    if width <= 0:
        return [""]
    lines: list[str] = []
    for raw in text.split("\n"):
        if raw == "":
            lines.append("")
            continue
        current: list[str] = []
        used = 0
        for ch in raw:
            w = char_width(ch)
            if used + w > width and current:
                lines.append("".join(current))
                current = []
                used = 0
            current.append(ch)
            used += w
        lines.append("".join(current))
    return lines


@dataclass
class Cell:
    ch: str = " "
    style: Style = DEFAULT


class Screen:
    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.w = width
        self.h = height
        self.cells: list[list[Cell]] = []
        self._prev: list[str] | None = None
        self.cursor: tuple[int, int] | None = None
        self._reset()

    def _reset(self) -> None:
        self.cells = [[Cell() for _ in range(self.w)] for _ in range(self.h)]

    def resize(self, width: int, height: int) -> None:
        if width == self.w and height == self.h:
            return
        self.w = max(20, width)
        self.h = max(8, height)
        self._reset()
        self._prev = None

    def invalidate(self) -> None:
        self._prev = None

    # ------------------------------------------------------------------
    def clear(self, style: Style = DEFAULT) -> None:
        for row in self.cells:
            for cell in row:
                cell.ch = " "
                cell.style = style

    def put(self, x: int, y: int, text: str, style: Style = DEFAULT) -> int:
        if y < 0 or y >= self.h:
            return x
        for ch in text:
            if ch == "\n":
                y += 1
                if y >= self.h:
                    break
                continue
            w = char_width(ch)
            if w == 0:
                continue
            if x + w > self.w:
                break
            if x < 0:
                x += w
                continue
            if self.cells[y][x].ch == "" and x > 0:
                self.cells[y][x - 1] = Cell(" ", self.cells[y][x - 1].style)
            self.cells[y][x] = Cell(ch, style)
            if w == 2:
                self.cells[y][x + 1] = Cell("", style)
            x += w
        return x

    def fill(
        self, x: int, y: int, width: int, height: int, style: Style = DEFAULT
    ) -> None:
        for row in range(y, y + height):
            if 0 <= row < self.h:
                self.put(x, row, " " * max(0, width), style)

    def hline(self, x: int, y: int, width: int, ch: str = "─", style: Style = DEFAULT) -> None:
        self.put(x, y, ch * max(0, width), style)

    def vline(self, x: int, y: int, height: int, ch: str = "│", style: Style = DEFAULT) -> None:
        for row in range(y, y + height):
            self.put(x, row, ch, style)

    def box(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        title: str = "",
        style: Style = DEFAULT,
        border_style: Style | None = None,
    ) -> None:
        if width < 2 or height < 2:
            return
        border = border_style or style
        self.put(x, y, "┌" + "─" * (width - 2) + "┐", border)
        for row in range(1, height - 1):
            self.put(x, y + row, "│", border)
            self.put(x + width - 1, y + row, "│", border)
        self.put(x, y + height - 1, "└" + "─" * (width - 2) + "┘", border)
        if title:
            label = f" {title} "
            self.put(x + 2, y, ellipsize(label, width - 4), border)

    # ------------------------------------------------------------------
    @staticmethod
    def _sgr(style: Style) -> str:
        parts = ["0"]
        if style.bold:
            parts.append("1")
        if style.dim:
            parts.append("2")
        if style.reverse:
            parts.append("7")
        if style.fg is not None:
            parts.extend(["38", "5", str(style.fg)])
        if style.bg is not None:
            parts.extend(["48", "5", str(style.bg)])
        return "\x1b[" + ";".join(parts) + "m"

    def _line(self, y: int) -> str:
        row = self.cells[y]
        last = -1
        for index, cell in enumerate(row):
            if cell.ch == "":
                continue
            if cell.ch != " " or cell.style.bg is not None:
                last = index
        if last < 0:
            return ""
        out: list[str] = []
        current: Style | None = None
        for index in range(last + 1):
            cell = row[index]
            if cell.ch == "":
                continue
            if cell.style != current:
                out.append(self._sgr(cell.style))
                current = cell.style
            out.append(cell.ch)
        out.append("\x1b[0m")
        return "".join(out)

    def flush(self) -> None:
        lines = [self._line(y) for y in range(self.h)]
        chunks: list[str] = []
        if self._prev is None:
            chunks.append("\x1b[2J")
            for y, line in enumerate(lines):
                chunks.append(f"\x1b[{y + 1};1H{line}\x1b[K")
        else:
            for y, line in enumerate(lines):
                if self._prev[y] != line:
                    chunks.append(f"\x1b[{y + 1};1H{line}\x1b[K")
        if self.cursor is not None and 0 <= self.cursor[0] < self.w and 0 <= self.cursor[1] < self.h:
            chunks.append(
                f"\x1b[{self.cursor[1] + 1};{self.cursor[0] + 1}H\x1b[?25h"
            )
        else:
            chunks.append("\x1b[?25l")
        if chunks:
            from .terminal import Terminal

            Terminal.write("".join(chunks))
        self._prev = lines
        self.cursor = None
