# -*- coding: utf-8 -*-
"""设置与诊断。"""

from __future__ import annotations


from .. import __version__
from .. import config
from ..core import auth, crypto, gitops
from ..tui.widgets import MenuItem
from ..util import log
from .base import MenuView


class SettingsView(MenuView):
    title = "设置 / 诊断"
    subtitle = f"hrm v{__version__}"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.refresh_items()

    def build_items(self) -> list[MenuItem]:
        items = [
            MenuItem("状态信息", "路径 / 依赖 / Token", action="status"),
            MenuItem("校验仓库", "索引 / 包 / 证书", action="verify"),
        ]
        if self.app.ctx.role.needs_login:
            items.append(MenuItem("重新登录", "调用 hpm-dev login", action="login"))
        if self.app.ctx.official_cert_path.is_file():
            items.append(MenuItem("完成初始化 / 签名索引", "使用 official.key", action="finish"))
        items.append(MenuItem("清除密钥缓存", "忘记本次会话的密码", action="clear"))
        items.append(MenuItem("打开日志目录", log.log_path().parent.as_posix(), action="log"))
        items.append(MenuItem("← 返回", "", action="back"))
        return items

    def header_lines(self) -> list[str]:
        ctx = self.app.ctx
        return [
            f"dev.json: {config.dev_json_path()}",
            f"Token: {self.app.token.label()}",
            f"仓库: {ctx.root} · {ctx.role.label}",
            f"密钥缓存: {self.app.key_cache_size} 个",
        ]

    def on_select(self, item: MenuItem) -> None:
        ctx = self.app.ctx
        if item.action == "back":
            self.app.pop()
        elif item.action == "status":
            self._status()
        elif item.action == "verify":
            from .packages import VerifyView

            self.app.push(VerifyView(self.app))
        elif item.action == "login":
            from .login import LoginView

            self.app.push(LoginView(self.app))
        elif item.action == "finish":
            from .init_repo import FinishInitView

            self.app.push(FinishInitView(self.app))
        elif item.action == "clear":
            self.app.clear_key_cache()
            self.app.toast("已清除密钥缓存", "ok")
        elif item.action == "log":
            self.app.info("日志位置", [str(log.log_path()), "按日期轮转，保留 7 天。"])
        self.refresh_items()

    def _status(self) -> None:
        ctx = self.app.ctx
        lines = [
            f"版本: hrm {__version__}",
            f"仓库根: {ctx.root}",
            f"仓库角色: {ctx.role.label}" + (f"（{ctx.domain}）" if ctx.domain else ""),
            f"dev.json: {config.dev_json_path()}",
            f"Token: {self.app.token.label()}",
            f"开发者: {(self.app.dev.developer if self.app.dev else '') or '-'}",
            f"仓库地址: {(self.app.dev.repo_url if self.app.dev else '') or '-'}",
            "",
            f"official.crt: {'存在' if ctx.official_cert_path.is_file() else '缺失'}",
            f"official.key: {'存在' if ctx.official_key_path.is_file() else '缺失'}",
            f"index.json: {'存在' if ctx.index_path.is_file() else '缺失'}",
            f"revoke.json: {'存在' if ctx.revoke_path.is_file() else '缺失'}",
            "",
            f"git: {gitops.available() and '可用' or '不可用'}",
            f"hpm-dev login: {'可用' if auth.detect() else '不可用'}",
            f"根证书: {crypto.cert_info(crypto.root_ca()).subject}",
        ]
        if ctx.cert:
            lines.append(f"仓库证书: {ctx.cert.subject}")
            lines.append(f"证书指纹: {ctx.cert.fingerprint}")
        self.app.message("状态信息", lines)
