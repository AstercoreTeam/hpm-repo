# -*- coding: utf-8 -*-
"""私域管理：域列表、签发域证书、吊销域证书。"""

from __future__ import annotations

from ..core import crypto, domains as domains_mod, index as index_mod, revoke
from ..tui.screen import Screen, ellipsize, st, text_width
from ..tui.widgets import MenuItem, draw_footer, draw_table, draw_title
from ..util import log
from .base import MenuView, View
from .common import commit_ui, official_key_ui


class DomainManagementView(MenuView):
    title = "私域管理"
    subtitle = "域列表 / 签发 / 吊销"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.refresh_items()

    def build_items(self) -> list[MenuItem]:
        return [
            MenuItem("私域列表", "domain.yaml 与已签发证书", action="list"),
            MenuItem("签发证书", "读取 requests/ 中的域请求", action="sign"),
            MenuItem("登记证书", "已签发但未登记到 domain.yaml", action="register"),
            MenuItem("吊销证书", "写入 revoke.json", action="revoke"),
            MenuItem("← 返回", "", action="back"),
        ]

    def header_lines(self) -> list[str]:
        ctx = self.app.ctx
        try:
            entries = domains_mod.list_domains(ctx.root)
            pending = domains_mod.pending_domain_requests(ctx.root)
        except Exception:  # noqa: BLE001
            entries, pending = [], 0
        return [
            f"仓库: {ctx.root}",
            f"域: {len(entries)} · 待签发请求: {pending}",
        ]

    def on_select(self, item: MenuItem) -> None:
        if item.action == "list":
            self.app.push(DomainListView(self.app))
        elif item.action == "sign":
            from .developers import sign_domain_wizard

            sign_domain_wizard(self.app)
        elif item.action == "register":
            register_domain_wizard(self.app)
        elif item.action == "revoke":
            revoke_domain_wizard(self.app)
        elif item.action == "back":
            self.app.pop()
        self.refresh_items()


class DomainListView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.entries: list[domains_mod.DomainEntry] = []
        self.cursor = 0
        self.offset = 0
        self.reload()

    def reload(self) -> None:
        self.entries = domains_mod.list_domains(self.app.ctx.root)
        if self.cursor >= len(self.entries):
            self.cursor = max(0, len(self.entries) - 1)

    def render(self, screen: Screen) -> None:
        draw_title(screen, "私域列表", f"{len(self.entries)} 个域")
        headers = ["域名", "状态", "序列号", "剩余", "本地证书"]
        name_w = max(10, min(20, max((text_width(e.name) for e in self.entries), default=10)))
        status_w = 18
        serial_w = 20
        days_w = 6
        cert_w = max(10, screen.w - (name_w + status_w + serial_w + days_w + 5 + 8))
        widths = [name_w, status_w, serial_w, days_w, cert_w]
        rows = []
        for e in self.entries:
            serial = e.cert.serial if e.cert else "-"
            days = str(e.cert.days_left) if e.cert else "-"
            local = e.local_cert.name if e.local_cert else "-"
            rows.append([e.name, e.status, serial, days, local])
        body = screen.h - 5
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + body:
            self.offset = self.cursor - body + 1
        if rows:
            draw_table(
                screen,
                3,
                3,
                screen.w - 6,
                headers,
                rows,
                widths,
                cursor=self.cursor,
                offset=self.offset,
                max_rows=body,
            )
        else:
            screen.put(
                4,
                3,
                "还没有域。可先使用「签发证书」处理 requests/ 中的域请求。",
                st(fg="muted"),
            )
        draw_footer(screen, "Enter 详情 · ↑↓ 移动 · r 刷新 · Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc" or (key.name == "char" and key.char == "q"):
            self.app.pop()
        elif key.name == "char" and key.char == "r":
            self.reload()
        elif not self.entries:
            return
        elif key.name in ("up", "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key.name in ("down", "j"):
            self.cursor = min(len(self.entries) - 1, self.cursor + 1)
        elif key.name in ("enter", "right"):
            self.app.push(DomainDetailView(self.app, self.entries[self.cursor]))


class DomainDetailView(View):
    def __init__(self, app, entry: domains_mod.DomainEntry) -> None:
        super().__init__(app)
        self.entry = entry

    def render(self, screen: Screen) -> None:
        e = self.entry
        draw_title(screen, f"域 {e.name}", e.status)
        lines = [
            f"域名:       {e.name}",
            f"状态:       {e.status}",
            f"索引:       {e.index or '-'}",
            f"证书 URL:   {e.cert_url or '-'}",
            f"本地证书:   {e.local_cert or '-'}",
        ]
        if e.cert:
            lines += [
                f"主题:       {e.cert.subject}",
                f"序列号:     {e.cert.serial}",
                f"有效期至:   {e.cert.not_after.astimezone().strftime('%Y-%m-%d %H:%M')}（剩 {e.cert.days_left} 天）",
                f"指纹:       {e.cert.fingerprint}",
            ]
        y = 3
        for line in lines:
            screen.put(4, y, ellipsize(line, screen.w - 8), st(fg="text"))
            y += 1
        draw_footer(screen, "Esc 返回")

    def handle_key(self, key) -> None:
        if key.name in ("esc", "backspace") or (
            key.name == "char" and key.char == "q"
        ):
            self.app.pop()


def revoke_domain_wizard(app) -> None:
    ctx = app.ctx
    entries = [
        e
        for e in domains_mod.list_domains(ctx.root)
        if e.local_cert is not None and e.cert is not None and not e.revoked
    ]
    if not entries:
        app.info(
            "没有可吊销的域证书",
            [f"请确认已签发域证书并放在 {ctx.root / 'outgoing'}"],
        )
        return

    options = [
        f"{e.name}  {e.cert.serial}  剩余 {e.cert.days_left} 天"
        for e in entries
    ]
    index = app.choose("选择要吊销的域证书", options)
    if index is None:
        return
    entry = entries[index]
    cert = entry.cert
    assert cert is not None

    if not app.confirm(
        "吊销域证书",
        [
            f"域名: {entry.name}",
            f"序列号: {cert.serial}",
            f"指纹: {cert.fingerprint}",
            "",
            "吊销后将写入 revoke.json 并重新签名。此操作不可撤销。",
        ],
        danger=True,
    ):
        return

    from .developers import REASONS

    labels = list(REASONS.keys())
    choice = app.choose("吊销原因", labels)
    if choice is None:
        return
    reason = REASONS[labels[choice]]

    key = official_key_ui(app, ctx)
    if key is None:
        return

    app.busy("吊销证书", "正在写入 revoke.json …")
    revoke.issue(
        ctx.root,
        serial=cert.serial,
        kind="domain",
        name=entry.name,
        san=cert.san[0] if cert.san else "",
        subject=cert.subject,
        cert_fingerprint=cert.fingerprint,
        reason=reason,
        revoked_by=(app.dev.developer if app.dev else ""),
    )
    revoke.sign(ctx.root, key)
    if ctx.index_path.is_file():
        index_mod.sign_index(ctx.root, key)
    log.log_action("revoke_domain", f"{entry.name} {cert.serial} {reason}")
    app.toast(f"已吊销域 {entry.name}", "ok", 3)
    commit_ui(app, ctx, f"Revoke domain {entry.name} (serial {cert.serial})")


def register_domain_wizard(app) -> None:
    ctx = app.ctx
    entries = [
        e
        for e in domains_mod.list_domains(ctx.root)
        if e.local_cert is not None and e.cert is not None and not e.registered
    ]
    if not entries:
        app.info("没有待登记的域证书", "已签发的域证书都已经在 domain.yaml 中。")
        return

    options = [f"{e.name}  {e.cert.serial}" for e in entries]
    index = app.choose("选择要登记的域证书", options)
    if index is None:
        return
    entry = entries[index]
    assert entry.cert is not None

    index_url = app.input(
        "登记域",
        "索引 URL",
        default="",
        hint="例如 https://raw.githubusercontent.com/<owner>/<repo>/main/index.json",
    )
    if index_url is None or not index_url.strip():
        return
    cert_url = app.input(
        "登记域",
        "证书 URL",
        default="",
        hint="例如 https://raw.githubusercontent.com/<owner>/<repo>/main/official.crt",
    )
    if cert_url is None or not cert_url.strip():
        return

    key = official_key_ui(app, ctx)
    if key is None:
        return
    app.busy("登记域", f"正在写入 domain.yaml …")
    try:
        domains_mod.register_domain(
            ctx.root, entry.name, index_url.strip(), cert_url.strip()
        )
        index_mod.rebuild(ctx.root)
        index_mod.sign_index(ctx.root, key)
    except Exception as exc:  # noqa: BLE001
        app.error("登记失败", str(exc))
        return
    log.log_action("domain_register", f"{entry.name}")
    app.toast(f"已登记域 {entry.name}", "ok", 3)
    commit_ui(
        app, ctx, f"Register domain {entry.name} in domain.yaml"
    )
