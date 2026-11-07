# -*- coding: utf-8 -*-
"""帮助页。"""

from __future__ import annotations

from ..tui.screen import Screen, st, text_width, wrap_text
from ..tui.widgets import draw_footer, draw_title
from .base import View

HELP = """
HPM Repo Manager（hrm）
=======================

主页
  ↑↓ / j k      移动光标
  Enter         确认
  r             刷新状态
  ?             打开本帮助
  q             退出程序

通用
  Esc           返回上一级
  方向键        在输入框中移动光标
  Ctrl-U        清空当前输入
  Ctrl-W        删除前一个词
  Ctrl-C        强制退出

仓库角色
  官方仓库       official.crt 的 SAN 为 hpm://official，由内置根证书签发
  私域仓库       official.crt 的 SAN 为 hpm://domain/<name>
  未初始化       没有 official.crt，可执行「初始化私域仓库」

Token
  从 ~/.hpm/dev.json 读取，只读。
  登录由 hpm-dev login 完成，登录成功后需要重新进入本工具或按 r 刷新。
  官方仓库完全免登录。

签名
  需要私钥时依次查找：
    official.key                   仓库根目录
    <dev>/<dev>.key                开发者目录
    ~/.hpm/keys/<dev>/<dev>.key    用户密钥目录
  找不到会要求输入路径；私钥有密码会要求输入密码（最多 3 次）。
  密码只保存在内存，可用「设置」清空缓存。

目录约定
  index.json / index.json.sig      索引与签名
  official.crt                     仓库证书
  revoke.json / revoke.json.sig    吊销列表与签名
  <developer>/                     开发者证书与包
  incoming/                        待处理签名请求
  requests/                        已处理请求归档
  outgoing/                        待发送的域证书

安全
  私钥永不入库；提交前会检查未忽略的 *.key。
  吊销列表与索引都必须能由 official.crt 验证。
"""


class HelpView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.offset = 0
        self.lines = [line for line in HELP.strip("\n").splitlines()]

    def _visible(self, screen: Screen) -> int:
        return max(4, screen.h - 4)

    def render(self, screen: Screen) -> None:
        draw_title(screen, "帮助", "Esc 返回")
        visible = self._visible(screen)
        y = 2
        for line in self.lines[self.offset : self.offset + visible]:
            screen.put(3, y, line, st(fg="text"))
            y += 1
        if self.offset + visible < len(self.lines):
            screen.put(
                screen.w - 12,
                screen.h - 2,
                "↓ 更多",
                st(fg="muted"),
            )
        draw_footer(screen, "↑↓ / PgUp PgDn 滚动 · Esc 返回")

    def handle_key(self, key) -> None:
        visible = self._visible(self.app.screen)
        if key.name == "esc":
            self.app.pop()
        elif key.name in ("down", "j"):
            self.offset = min(max(0, len(self.lines) - visible), self.offset + 1)
        elif key.name in ("up", "k"):
            self.offset = max(0, self.offset - 1)
        elif key.name == "pgdn":
            self.offset = min(max(0, len(self.lines) - visible), self.offset + visible)
        elif key.name == "pgup":
            self.offset = max(0, self.offset - visible)
        elif key.name == "home":
            self.offset = 0
        elif key.name == "end":
            self.offset = max(0, len(self.lines) - visible)
