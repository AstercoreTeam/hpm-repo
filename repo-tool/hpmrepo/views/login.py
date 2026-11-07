# -*- coding: utf-8 -*-
"""登录页：委托 hpm-dev login。"""

from __future__ import annotations

import subprocess

from ..core import auth
from ..tui.screen import Screen, ellipsize, st
from ..tui.widgets import draw_footer, draw_title
from ..util import log
from .base import View


class LoginView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.command = auth.detect()

    def render(self, screen: Screen) -> None:
        draw_title(screen, "登录开发者中心", "委托 hpm-dev login")
        lines = [
            "登录将调用 hpm-dev login，由它完成：",
            "  1. 打开浏览器进行 OAuth 授权",
            "  2. 获取 AccessToken",
            "  3. 确认开发者名与私域仓库地址",
            "  4. 写入 ~/.hpm/dev.json",
            "",
            f"当前 Token：{self.app.token.label()}",
        ]
        if self.app.dev and self.app.dev.developer:
            lines.append(f"当前开发者：{self.app.dev.developer}")
        if self.app.dev and self.app.dev.repo_url:
            lines.append(f"仓库地址：{self.app.dev.repo_url}")
        lines.append("")
        if self.command:
            lines.append(f"将执行：{self.command.label}")
            lines.append("")
            lines.append("按 Enter 开始登录，g 生成签名请求（无需登录），Esc 返回。")
        else:
            lines.append(auth.hint())
            lines.append("")
            lines.append("也可以设置 HPM_DEV_BIN 指向支持 login 的程序。")
        y = 3
        for line in lines:
            screen.put(4, y, ellipsize(line, screen.w - 8), st(fg="text"))
            y += 1
        draw_footer(screen, "Enter 开始登录 · g 生成签名请求 · Esc 返回 · r 重新探测")

    def handle_key(self, key) -> None:
        if key.name == "esc":
            self.app.pop()
        elif key.name == "char" and key.char == "r":
            self.command = auth.detect()
        elif key.name == "char" and key.char == "g":
            from .request import RequestView

            self.app.push(RequestView(self.app))
        elif key.name == "enter":
            if self.command is None:
                self.command = auth.detect()
                if self.command is None:
                    self.app.info(auth.hint())
                    return
            self._login()

    def _login(self) -> None:
        if not self.app.confirm(
            "登录开发者中心",
            [
                f"即将执行：{self.command.label}",
                "程序会暂时退出全屏界面，把终端交给登录命令。",
                "完成后按 Enter 返回。",
            ],
        ):
            return

        command = self.command
        code = 1
        self.app.suspend()
        try:
            print("\n" + "=" * 60)
            print(f"执行：{command.label}")
            print("=" * 60 + "\n")
            try:
                code = subprocess.call(command.argv, cwd=str(self.app.ctx.root))
            except FileNotFoundError:
                print(f"找不到命令: {command.argv[0]}")
                code = 127
            except KeyboardInterrupt:
                code = 130
            try:
                input("\n按 Enter 返回 hrm…")
            except (EOFError, KeyboardInterrupt):
                pass
        finally:
            self.app.resume()

        self.app.reload()
        if self.app.token.ok:
            log.log_action("login", "success")
            self.app.toast("登录成功", "ok")
            self.app.home()
            return

        if code == 0:
            detail = "登录命令已结束，但 dev.json 中没有有效的 Token。"
        else:
            detail = f"登录命令退出码 {code}。"
        self.app.error(detail, "请确认 hpm-dev 可用，并重新运行登录。")
