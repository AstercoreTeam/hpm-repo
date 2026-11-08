# -*- coding: utf-8 -*-
"""应用主循环、导航栈与弹窗。"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from typing import Callable

from . import __app_long_name__, __version__, config, repo as repo_mod
from .core import identity as identity_mod
from .tui import theme
from .tui.screen import Screen, ellipsize, st, text_width, wrap_text
from .tui.terminal import Key, Terminal
from .tui.widgets import draw_modal, draw_toast, modal_geometry
from .util import log
from .util.errors import ToolError


class App:
    def __init__(self, *, animate: bool = True) -> None:
        self.terminal = Terminal()
        width, height = self.terminal.size()
        self.screen = Screen(width, height)
        self.stack: list = []
        self.running = True
        self.animate = animate
        self.toast_message = ""
        self.toast_level = "info"
        self.toast_until = 0.0
        self.ctx: repo_mod.RepoContext
        self.dev: config.DevConfig | None
        self.token: config.TokenStatus
        self.identity: identity_mod.Identity
        self._key_cache: dict[str, object] = {}
        self.debug = False
        self.reload()

    # ------------------------------------------------------------------
    # 状态
    def reload(self) -> None:
        try:
            self.dev = config.load_dev()
        except config.ConfigError as exc:
            self.dev = None
            self.toast(exc.message, "error")
        self.token = config.token_status(self.dev)
        self.ctx = repo_mod.detect()
        self.identity = identity_mod.detect(
            self.ctx, self.dev.developer if self.dev else ""
        )

    # ------------------------------------------------------------------
    # 导航
    def push(self, view) -> None:
        self.stack.append(view)

    def pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self.screen.invalidate()
            parent = self.stack[-1]
            resume = getattr(parent, "on_resume", None)
            if callable(resume):
                resume()

    def replace(self, view) -> None:
        if self.stack:
            self.stack[-1] = view
        else:
            self.stack.append(view)
        self.screen.invalidate()

    def home(self) -> None:
        from .views.home import HomeView

        self.reload()
        # 回到主页视为一次操作结束，清除会话内已解密私钥，下次签名重新要求密码
        self.clear_key_cache()
        self.stack = [HomeView(self)]
        self.screen.invalidate()

    # ------------------------------------------------------------------
    # 提示
    def toast(self, message: str, level: str = "info", seconds: float = 4.0) -> None:
        self.toast_message = message
        self.toast_level = level
        self.toast_until = time.monotonic() + seconds

    # ------------------------------------------------------------------
    # 主循环
    def run(self) -> None:
        from .views.splash import SplashView

        self.terminal.enter()
        try:
            self.stack = [SplashView(self)]
            while self.running and self.stack:
                self._sync_size()
                now = time.monotonic()
                view = self.stack[-1] if self.stack else None
                if view is None:
                    break
                try:
                    view.update(now)
                    view = self.stack[-1] if self.stack else None
                    if view is None:
                        break
                    self._draw(view, now)
                except ToolError as exc:
                    self.error(exc.message, exc.hint)
                key = self.terminal.read_key(0.04)
                if key is None:
                    continue
                if key.name == "ctrl_c":
                    self.running = False
                    break
                try:
                    view.handle_key(key)
                except ToolError as exc:
                    log.log_action("error", f"{type(exc).__name__}: {exc.message}")
                    self.error(exc.message, exc.hint)
                except Exception as exc:  # noqa: BLE001
                    log.get_logger().exception("unhandled")
                    if self.debug:
                        self.error("发生内部错误", traceback.format_exc(limit=3))
                    else:
                        self.error("发生内部错误", f"{exc}（日志: {log.log_path()}）")
        finally:
            self.terminal.exit()

    def _sync_size(self) -> None:
        width, height = self.terminal.size()
        if (width, height) != (self.screen.w, self.screen.h):
            self.screen.resize(width, height)

    def _draw(self, view, now: float) -> None:
        self.screen.clear()
        try:
            view.render(self.screen)
        except Exception as exc:  # noqa: BLE001 - 渲染失败不能递归弹错误框
            log.get_logger().exception("render failed")
            self.screen.clear()
            self.screen.put(2, 2, "界面渲染失败", st(fg="red", bold=True))
            self.screen.put(2, 4, f"{type(exc).__name__}: {exc}", st(fg="text"))
            self.screen.put(2, 6, f"日志: {log.log_path()}", st(fg="muted"))
            self.screen.flush()
            return
        if self.toast_message and now < self.toast_until:
            draw_toast(self.screen, self.toast_message, self.toast_level)
        elif now >= self.toast_until:
            self.toast_message = ""
        self.screen.flush()

    def _redraw_current(self) -> None:
        if not self.stack:
            return
        self._draw(self.stack[-1], time.monotonic())

    # ------------------------------------------------------------------
    # 子进程
    def suspend(self) -> None:
        self.terminal.exit()

    def resume(self) -> None:
        self.terminal.enter()
        width, height = self.terminal.size()
        self.screen.resize(width, height)
        self.screen.invalidate()

    # ------------------------------------------------------------------
    # 弹窗
    def message(self, title: str, lines: list[str] | str, level: str = "info") -> None:
        if isinstance(lines, str):
            lines = [lines]
        while True:
            self._redraw_current()
            draw_modal(
                self.screen,
                title,
                lines,
                width=min(72, self.screen.w - 4),
                hint="按 Enter 继续",
            )
            # 标题颜色通过重绘覆盖
            self.screen.flush()
            key = self.terminal.read_key(0.2)
            if key is None:
                continue
            if key.name in ("enter", "esc", "space", "ctrl_c"):
                return
            if key.name == "char" and key.char.lower() in ("q",):
                return

    def error(self, message: str, hint: str = "") -> None:
        lines = [message]
        if hint:
            lines += ["", hint]
        lines.append("")
        lines.append(f"日志: {log.log_path()}")
        self.message("错误", lines, "error")

    def info(
        self, title: str, lines: list[str] | str | None = None, level: str = "info"
    ) -> None:
        if lines is None:
            body = [title]
            heading = "提示"
        else:
            heading = title
            body = [lines] if isinstance(lines, str) else list(lines)
        self.message(heading, body, level)

    def confirm(
        self, title: str, message: str | list[str], *, danger: bool = False
    ) -> bool:
        lines = [message] if isinstance(message, str) else list(message)
        options = ["取消", "确定"]
        cursor = 0
        while True:
            self._redraw_current()
            draw_modal(
                self.screen,
                title,
                lines,
                width=min(70, self.screen.w - 4),
                options=options,
                cursor=cursor,
                hint="← → 选择 · Enter 确认 · Esc 取消",
            )
            self.screen.flush()
            key = self.terminal.read_key(0.2)
            if key is None:
                continue
            if key.name == "esc":
                return False
            if key.name in ("left", "up"):
                cursor = 0
            elif key.name in ("right", "down", "tab"):
                cursor = 1
            elif key.name == "enter":
                return cursor == 1
            elif key.name == "char" and key.char.lower() == "y":
                return True
            elif key.name == "char" and key.char.lower() == "n":
                return False

    def choose(
        self, title: str, options: list[str], *, hint: str = ""
    ) -> int | None:
        if not options:
            return None
        cursor = 0
        width = max(52, max(text_width(o) for o in options) + 12)
        while True:
            self._redraw_current()
            draw_modal(
                self.screen,
                title,
                [""],
                width=min(width, self.screen.w - 4),
                options=options,
                cursor=cursor,
                hint=hint or "↑↓ 选择 · Enter 确认 · Esc 取消",
                vertical_options=True,
            )
            self.screen.flush()
            key = self.terminal.read_key(0.2)
            if key is None:
                continue
            hold_until = time.monotonic() + 0.4
            if key.name == "esc":
                return None
            if key.name in ("up", "left"):
                cursor = (cursor - 1) % len(options)
            elif key.name in ("down", "right", "tab"):
                cursor = (cursor + 1) % len(options)
            elif key.name == "enter":
                return cursor

    def input(
        self,
        title: str,
        prompt: str,
        *,
        default: str = "",
        mask: bool = False,
        hint: str = "",
    ) -> str | None:
        text = list(default)
        cursor = len(text)
        prompt_lines = wrap_text(prompt, max(20, min(70, self.screen.w - 10)))
        hold_until = time.monotonic() + 0.5

        while True:
            width = min(74, self.screen.w - 4)
            inner = width - 6
            height = len(prompt_lines) + 6
            x, y, w, h = modal_geometry(self.screen, width, height)
            self._redraw_current()
            self.screen.fill(x + 1, y + 1, w - 2, h - 2, st(fg="text", bg="modal"))
            self.screen.box(
                x,
                y,
                w,
                h,
                title,
                st(fg="text", bg="modal"),
                st(fg="cyan", bg="modal", bold=True),
            )
            row = y + 2
            for line in prompt_lines:
                self.screen.put(x + 3, row, line, st(fg="text", bg="modal"))
                row += 1
            row += 1
            shown = "•" * len(text) if mask else "".join(text)
            self.screen.fill(x + 3, row, inner, 1, st(fg="white", bg="panel"))
            self.screen.put(
                x + 3, row, ellipsize(shown, inner), st(fg="white", bg="panel")
            )
            cursor_x = x + 3 + text_width(
                ("•" * cursor) if mask else "".join(text[:cursor])
            )
            # 软件闪烁：输入后短暂常亮，其余时间约 0.5s 切换一次
            now = time.monotonic()
            visible = now < hold_until or int(now * 2) % 2 == 0
            self.screen.cursor = (
                (min(cursor_x, x + w - 3), row) if visible else None
            )
            if hint:
                self.screen.put(
                    x + 3,
                    y + h - 2,
                    ellipsize(hint, w - 6),
                    st(fg="muted", bg="modal"),
                )
            self.screen.flush()

            key = self.terminal.read_key(0.2)
            if key is None:
                continue
            hold_until = time.monotonic() + 0.4
            if key.name == "esc":
                return None
            if key.name == "enter":
                return "".join(text)
            if key.name == "backspace":
                if cursor > 0:
                    del text[cursor - 1]
                    cursor -= 1
            elif key.name == "delete":
                if cursor < len(text):
                    del text[cursor]
            elif key.name == "left":
                cursor = max(0, cursor - 1)
            elif key.name == "right":
                cursor = min(len(text), cursor + 1)
            elif key.name == "home":
                cursor = 0
            elif key.name == "end":
                cursor = len(text)
            elif key.name == "ctrl_u":
                text.clear()
                cursor = 0
            elif key.name == "ctrl_w":
                while cursor > 0 and text[cursor - 1].isspace():
                    del text[cursor - 1]
                    cursor -= 1
                while cursor > 0 and not text[cursor - 1].isspace():
                    del text[cursor - 1]
                    cursor -= 1
            elif key.name == "space":
                text.insert(cursor, " ")
                cursor += 1
            elif key.name == "char":
                text.insert(cursor, key.char)
                cursor += 1

    def busy(self, title: str, message: str) -> None:
        self._redraw_current()
        draw_modal(self.screen, title, [message], width=min(60, self.screen.w - 4))
        self.screen.flush()

    # ------------------------------------------------------------------
    # 密码 / 密钥
    def cached_key(self, path: str):
        return self._key_cache.get(path)

    def cache_key(self, path: str, key) -> None:
        self._key_cache[path] = key

    def clear_key_cache(self) -> None:
        self._key_cache.clear()

    @property
    def key_cache_size(self) -> int:
        return len(self._key_cache)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hrm",
        description=f"{__app_long_name__}（HPM 仓库维护工具）",
    )
    parser.add_argument("--no-animation", action="store_true", help="跳过启动动画")
    parser.add_argument("--no-color", action="store_true", help="禁用颜色")
    parser.add_argument("--debug", action="store_true", help="显示内部错误堆栈")
    parser.add_argument("--version", action="version", version=f"hrm {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.no_color:
        import os

        os.environ["NO_COLOR"] = "1"
    app = App(animate=not args.no_animation)
    app.debug = args.debug
    if not app.terminal.is_tty:
        print("hrm 需要交互式终端（TTY）。请在终端中运行。")
        return 2
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        app.terminal.exit()
        log.get_logger().exception("fatal")
        if args.debug:
            traceback.print_exc()
        else:
            print(f"hrm 发生内部错误，详见 {log.log_path()}")
        return 1
    finally:
        app.terminal.exit()
    return 0
