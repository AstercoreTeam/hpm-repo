# -*- coding: utf-8 -*-
"""帮助页。"""

from __future__ import annotations

from ..i18n import language, tr
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

签名请求
  主页「生成签名请求」或登录页按 g（无需登录）。
  请求写入 repo-tool/requests/，管理员签发时直接显示列表。
  新私钥自动保存到 ~/.hpm/keys/，必须设置至少 8 位密码。

签名
  私钥只从用户目录 ~/.hpm 下查找：
    ~/.hpm/keys/official.key               官方仓库私钥
    ~/.hpm/keys/domain/<domain>/official.key  域私钥
    ~/.hpm/keys/dev/<dev>/<dev>.key           开发者私钥
  旧布局 ~/.hpm/keys/<name>/ 仍兼容读取。
  仓库目录中不再查找私钥；找不到会要求输入路径。
  支持 PEM 与 OpenSSH 私钥；有密码会要求输入密码（最多 3 次）。
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


HELP_EN = """
HPM Repo Manager (hrm)
======================

Home
  Up/Down or j/k   Move cursor
  Enter            Select
  r                Refresh
  ?                This help
  q                Quit

General
  Esc              Back / cancel
  Arrow keys       Move cursor in input
  Ctrl-U           Clear input
  Ctrl-W           Delete previous word
  Ctrl-C           Force quit

Repository roles
  Official         official.crt SAN hpm://official, signed by built-in root
  Private          official.crt SAN hpm://domain/<name>
  Uninitialized    No official.crt yet; initialize a private repository

Identities
  Unregistered     Can only generate signing requests
  Developer        No sign-in; publish packages (sign only)
  Private Admin    Sign in required; manage packages/developers, sign index/revocation
  Official Admin   Manage repository, domains, and trust root

Token
  Read-only from ~/.hpm/dev.json.
  Login is delegated to hpm-dev login; restart or press r after login.
  Official admins do not need to sign in.

Signing requests
  Home -> Generate Signing Request, or g on the login page (no sign-in).
  Requests go to repo-tool/requests/ and are listed when an admin signs.
  New private keys are saved under ~/.hpm/keys/ and require an 8+ char password.

Keys
  Private keys are only read from ~/.hpm:
    ~/.hpm/keys/official.key
    ~/.hpm/keys/domain/<domain>/official.key
    ~/.hpm/keys/dev/<developer>/<developer>.key
  Legacy keys/<name>/ layouts are still readable.
  PEM and OpenSSH keys are supported. Encrypted keys prompt for a password.

Trust Root
  Official admins can issue/revoke official certificates and refresh ROOT_revoke.json.
  The root private key path and password are requested for every operation and are never cached.

Layout
  index.json / index.json.sig      Index and signature
  official.crt                     Repository certificate
  revoke.json / revoke.json.sig    Repository revocation list
  ROOT_revoke.json / .sig          Root revocation list
  <developer>/                     Developer certificate and packages
  repo-tool/requests/              Pending signing requests
  requests/                        Processed requests
  outgoing/                        Issued domain certificates

Security
  Private keys are never committed; unignored *.key files are rejected.
  Index and revocation lists must verify against official.crt.
"""


class HelpView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.offset = 0
        text = HELP if language() == "zh" else HELP_EN
        self.lines = [line for line in text.strip("\n").splitlines()]

    def _visible(self, screen: Screen) -> int:
        return max(4, screen.h - 4)

    def render(self, screen: Screen) -> None:
        draw_title(screen, "帮助", "Esc 返回")
        visible = self._visible(screen)
        y = 2
        for line in self.lines[self.offset : self.offset + visible]:
            screen.put(3, y, tr(line), st(fg="text"))
            y += 1
        if self.offset + visible < len(self.lines):
            screen.put(
                screen.w - 12,
                screen.h - 2,
                tr("↓ 更多"),
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
