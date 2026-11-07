# -*- coding: utf-8 -*-
"""主页：按仓库角色与 Token 状态显示菜单。"""

from __future__ import annotations

from .. import __app_long_name__
from ..core import gitops, index as index_mod
from ..repo import Role
from ..tui.screen import Screen, ellipsize, st
from ..tui.widgets import MenuItem, draw_footer, draw_menu, draw_title, menu_move
from .base import View


class HomeView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.cursor = 0
        self.header: list[str] = []
        self.items: list[MenuItem] = []
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.app.reload()
        self.ctx = self.app.ctx
        self.dev = self.app.dev
        self.token = self.app.token
        self.header = self._build_header()
        self.items = self._build_items()
        if self.items and not self.items[self.cursor].enabled:
            self.cursor = menu_move(self.items, self.cursor, 1)

    def _git_text(self) -> str:
        if not gitops.available():
            return "Git: 未安装"
        status = gitops.status(self.ctx.root)
        if not status.is_repo:
            return "Git: 未初始化"
        mark = "干净" if status.clean else f"{status.dirty} 项待提交"
        return f"Git: {status.branch or '-'} · {mark} · ↑{status.ahead} ↓{status.behind}"

    def _index_text(self) -> str:
        if not self.ctx.index_path.is_file():
            return "索引: 未生成"
        ok, detail = index_mod.verify_index(self.ctx.root)
        try:
            data = index_mod.load_index(self.ctx.root)
            packages = index_mod.package_count(data)
            developers = index_mod.developer_count(data)
        except Exception:  # noqa: BLE001
            packages = developers = 0
        return (
            f"索引: {'签名有效' if ok else detail} · 包 {packages} · 开发者 {developers}"
        )

    def _cert_text(self) -> str:
        cert = self.ctx.cert
        if cert is None:
            return "证书: 未找到 official.crt"
        san = cert.san[0] if cert.san else "无 SAN"
        return f"证书: {san} · 剩余 {cert.days_left} 天"

    def _build_header(self) -> list[str]:
        root = str(self.ctx.root)
        role = self.ctx.role
        cert = self.ctx.cert
        lines: list[str] = []

        if role is Role.OFFICIAL:
            lines.append(self._cert_text())
            key_mark = "已找到" if self.ctx.official_key_path.is_file() else "未找到"
            lines.append(f"本地: {root}   角色: 官方仓库   私钥: {key_mark}")
            remote = ""
            if gitops.available() and gitops.is_repo(self.ctx.root):
                status = gitops.status(self.ctx.root)
                for line in status.remotes.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        remote = f"{parts[0]} {parts[1]}"
                        break
            lines.append(f"远程: {remote or '未配置'}")
            lines.append(f"{self._index_text()}   {self._git_text()}")
            return lines

        if role in (Role.PRIVATE, Role.PRIVATE_UNVERIFIED):
            developer = self.dev.developer if self.dev else ""
            github = self.dev.github_url if self.dev else ""
            repo_url = self.dev.repo_url if self.dev else ""
            lines.append(f"开发者: {developer or '未登录'}   GitHub: {github or '-'}")
            lines.append(f"私域仓库: {repo_url or '-'}")
            extra = f"（{self.ctx.domain}）" if self.ctx.domain else ""
            warn = "  ! 未验证签发者" if role is Role.PRIVATE_UNVERIFIED else ""
            lines.append(f"本地: {root}   角色: 私域仓库{extra}{warn}")
            lines.append(f"Token: {self.token.label()}   {self._cert_text()}")
            lines.append(f"{self._index_text()}   {self._git_text()}")
            return lines

        if role is Role.UNINITIALIZED:
            lines.append(f"本地: {root}   角色: 未初始化")
            lines.append(f"Token: {self.token.label()}")
            request = self.ctx.root / "official.request.json"
            if request.is_file():
                lines.append("状态: 已生成签名请求，等待官方签发 official.crt")
                lines.append(f"请求: {request}")
            else:
                lines.append("提示: 初始化会生成 Git 仓库、official 密钥对与官方签名请求")
            return lines

        lines.append(f"本地: {root}   角色: 证书异常")
        lines.append(f"证书: {self.ctx.cert_error or '无法解析'}")
        lines.append("请进入「设置 / 诊断」查看详情并修复")
        return lines

    def _pending_init(self) -> bool:
        if self.ctx.role not in (Role.PRIVATE, Role.PRIVATE_UNVERIFIED):
            return False
        return not (
            self.ctx.index_sig_path.is_file()
            and self.ctx.revoke_path.is_file()
            and self.ctx.revoke_sig_path.is_file()
        )

    def _build_items(self) -> list[MenuItem]:
        role = self.ctx.role
        valid = self.token.ok
        items: list[MenuItem] = []

        if role is Role.OFFICIAL:
            items.append(MenuItem("管理包", "签名、索引、发布", action="packages"))
            items.append(MenuItem("管理开发者", "证书签发与吊销", action="developers"))
            items.append(MenuItem("设置 / 诊断", "", action="settings"))
            items.append(MenuItem("退出", "", action="exit"))
            return items

        if role in (Role.PRIVATE, Role.PRIVATE_UNVERIFIED):
            if not valid:
                items.append(
                    MenuItem("登录开发者中心", "需要先登录", action="login")
                )
                items.append(MenuItem("退出", "", action="exit"))
                return items
            if self._pending_init():
                items.append(
                    MenuItem(
                        "完成初始化",
                        "导入 official.crt 后签名",
                        action="finish_init",
                    )
                )
            items.append(MenuItem("登录开发者中心", "重新登录 / 切换账号", action="login"))
            items.append(MenuItem("管理包", "", action="packages"))
            items.append(MenuItem("管理开发者", "", action="developers"))
            items.append(MenuItem("设置 / 诊断", "", action="settings"))
            items.append(MenuItem("退出", "", action="exit"))
            return items

        if role is Role.UNINITIALIZED:
            if valid:
                if self.ctx.index_path.is_file():
                    items.append(
                        MenuItem(
                            "完成初始化",
                            "等待 official.crt",
                            action="finish_init",
                        )
                    )
                else:
                    items.append(MenuItem("初始化私域仓库", "生成密钥与签名请求", action="init"))
                items.append(MenuItem("设置 / 诊断", "", action="settings"))
                items.append(MenuItem("退出", "", action="exit"))
            else:
                items.append(
                    MenuItem("登录开发者中心", "初始化前需要登录", action="login")
                )
                items.append(MenuItem("退出", "", action="exit"))
            return items

        items.append(MenuItem("设置 / 诊断", "查看证书问题", action="settings"))
        items.append(MenuItem("退出", "", action="exit"))
        return items

    # ------------------------------------------------------------------
    def render(self, screen: Screen) -> None:
        draw_title(screen, __app_long_name__, "hpm-repo")
        box_w = screen.w - 4
        box_h = len(self.header) + 2
        x = 2
        y = 1
        screen.fill(x + 1, y + 1, box_w - 2, box_h - 2, st(bg="header"))
        screen.box(
            x,
            y,
            box_w,
            box_h,
            "",
            st(fg="text", bg="header"),
            st(fg="dim", bg="header"),
        )
        for index, line in enumerate(self.header):
            screen.put(
                x + 2,
                y + 1 + index,
                ellipsize(line, box_w - 4),
                st(fg="text", bg="header"),
            )

        menu_y = y + box_h + 1
        draw_menu(screen, 3, menu_y, screen.w - 6, self.items, self.cursor)
        draw_footer(
            screen,
            "↑↓ 移动 · Enter 确认 · r 刷新 · ? 帮助 · q 退出",
        )

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
                self._activate(item.action)
        elif key.name == "char" and key.char == "r":
            self.refresh()
            self.app.toast("已刷新", "ok", 1.5)
        elif key.name == "char" and key.char == "?":
            from .help import HelpView

            self.app.push(HelpView(self.app))
        elif key.name == "char" and key.char == "q":
            self.app.running = False

    def _activate(self, action: str) -> None:
        if action == "login":
            from .login import LoginView

            self.app.push(LoginView(self.app))
        elif action == "packages":
            from .packages import PackagesView

            self.app.push(PackagesView(self.app))
        elif action == "developers":
            from .developers import DevelopersView

            self.app.push(DevelopersView(self.app))
        elif action == "init":
            from .init_repo import InitRepoView

            self.app.push(InitRepoView(self.app))
        elif action == "finish_init":
            from .init_repo import FinishInitView

            self.app.push(FinishInitView(self.app))
        elif action == "settings":
            from .settings import SettingsView

            self.app.push(SettingsView(self.app))
        elif action == "exit":
            self.app.running = False
