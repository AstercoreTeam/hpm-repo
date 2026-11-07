# -*- coding: utf-8 -*-
"""开发者与证书管理界面。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..core import crypto, developers as dev_mod, domains as domains_mod, index as index_mod, revoke
from ..tui.screen import Screen, ellipsize, st, text_width
from ..tui.widgets import MenuItem, draw_footer, draw_table, draw_title
from ..util.fs import atomic_write
from ..util.jsonc import dumps
from ..util import log
from .base import MenuView, View
from .common import (
    commit_ui,
    official_key_ui,
    rebuild_and_sign,
    resolve_request_path,
)

REASONS = {
    "密钥泄露 / 私钥丢失（keyCompromise）": "keyCompromise",
    "证书被替换 / 重新签发（superseded）": "superseded",
    "开发者停止使用（ceased）": "ceased",
    "其他 / 未指定（unspecified）": "unspecified",
}


class DevelopersView(MenuView):
    title = "管理开发者"
    subtitle = "证书签发 / 吊销"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.refresh_items()

    def build_items(self) -> list[MenuItem]:
        items = [
            MenuItem("开发者列表", "", action="list"),
            MenuItem("添加开发者", "对签名请求签发证书", action="add"),
            MenuItem("吊销证书", "写入 revoke.json", action="revoke"),
            MenuItem("删除开发者", "删除目录并重建索引", action="delete"),
        ]
        items.append(MenuItem("← 返回", "", action="back"))
        return items

    def header_lines(self) -> list[str]:
        ctx = self.app.ctx
        try:
            developers = dev_mod.list_developers(ctx.root)
        except Exception:  # noqa: BLE001
            developers = []
        revoked = sum(1 for d in developers if d.revoked)
        return [
            f"仓库: {ctx.root}",
            f"角色: {ctx.role.label} · 开发者 {len(developers)} · 已吊销 {revoked}",
        ]

    def on_select(self, item: MenuItem) -> None:
        if item.action == "list":
            self.app.push(DeveloperListView(self.app))
        elif item.action == "add":
            add_developer_wizard(self.app)
        elif item.action == "revoke":
            revoke_wizard(self.app)
        elif item.action == "delete":
            delete_developer_wizard(self.app)
        elif item.action == "back":
            self.app.pop()
        self.refresh_items()


class DeveloperListView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.developers: list[dev_mod.Developer] = []
        self.cursor = 0
        self.offset = 0
        self.reload()

    def reload(self) -> None:
        self.developers = dev_mod.list_developers(self.app.ctx.root)
        if self.cursor >= len(self.developers):
            self.cursor = max(0, len(self.developers) - 1)

    def render(self, screen: Screen) -> None:
        draw_title(screen, "开发者列表", f"{len(self.developers)} 个")
        headers = ["名称", "SAN", "证书序列号", "剩余", "包", "状态"]
        name_w = 12
        san_w = max(20, min(32, max((text_width(d.cert.san[0]) for d in self.developers if d.cert.san), default=20)))
        serial_w = 18
        days_w = 6
        pkg_w = 4
        state_w = max(8, screen.w - (name_w + san_w + serial_w + days_w + pkg_w + 5 + 8))
        widths = [name_w, san_w, serial_w, days_w, pkg_w, state_w]
        rows = []
        for d in self.developers:
            san = d.cert.san[0] if d.cert.san else "-"
            state = "已吊销" if d.revoked else ("过期" if d.cert.expired else "有效")
            if not d.key_present:
                state += "/无密钥"
            rows.append([d.name, san, d.cert.serial, str(d.cert.days_left), str(d.packages), state])
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
            screen.put(4, 3, "还没有开发者。可把签名请求放进 incoming/ 后使用「添加开发者」。", st(fg="muted"))
        draw_footer(screen, "Enter 详情 · ↑↓ 移动 · r 刷新 · Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc" or (key.name == "char" and key.char == "q"):
            self.app.pop()
        elif key.name == "char" and key.char == "r":
            self.reload()
        elif not self.developers:
            return
        elif key.name in ("up", "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key.name in ("down", "j"):
            self.cursor = min(len(self.developers) - 1, self.cursor + 1)
        elif key.name in ("enter", "right"):
            self.app.push(DeveloperDetailView(self.app, self.developers[self.cursor]))


class DeveloperDetailView(View):
    def __init__(self, app, developer: dev_mod.Developer) -> None:
        super().__init__(app)
        self.developer = developer

    def render(self, screen: Screen) -> None:
        d = self.developer
        cert = d.cert
        draw_title(screen, f"开发者 {d.name}", "证书详情")
        lines = [
            f"目录:       {d.directory}",
            f"证书:       {d.directory / (d.name + '.crt')}",
            f"主题:       {cert.subject}",
            f"签发者:     {cert.issuer}",
            f"SAN:        {', '.join(cert.san) or '-'}",
            f"序列号:     {cert.serial}",
            f"算法:       {cert.algorithm}",
            f"有效期至:   {cert.not_after.astimezone().strftime('%Y-%m-%d %H:%M')}（剩 {cert.days_left} 天）",
            f"指纹:       {cert.fingerprint}",
            f"吊销:       {'是' if d.revoked else '否'}",
            f"私钥:       {'已找到' if d.key_present else '未找到'}",
            f"包数量:     {d.packages}",
        ]
        y = 3
        for line in lines:
            screen.put(4, y, ellipsize(line, screen.w - 8), st(fg="text"))
            y += 1
        draw_footer(screen, "r 吊销此证书 · Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc":
            self.app.pop()
        elif key.name == "char" and key.char == "r":
            revoke_developer(self.app, self.developer)
            self.app.pop()


def _receipt(kind: str, name: str, info: crypto.CertInfo, cert_path: Path) -> dict:
    return {
        "schema": 1,
        "kind": kind,
        "name": name,
        "san": info.san[0] if info.san else "",
        "subject": info.subject,
        "serial": info.serial,
        "fingerprintSha256": info.fingerprint,
        "notBefore": info.not_before.astimezone().isoformat(),
        "notAfter": info.not_after.astimezone().isoformat(),
        "certificate": str(cert_path),
        "issuedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def add_developer_wizard(app) -> None:
    ctx = app.ctx
    if not ctx.role.can_manage:
        app.error("当前仓库不能签发证书", ctx.role.label)
        return

    path = resolve_request_path(app, ctx, kind="developer")
    if path is None:
        return
    try:
        req = dev_mod.load_request(path, ctx.root)
    except Exception as exc:  # noqa: BLE001
        app.error("签名请求无效", str(exc))
        return
    if req.kind != "developer":
        app.error("这不是开发者签名请求", f"类型为 {req.kind}，请到「私域管理 → 签发证书」。")
        return

    existing = ctx.developer_cert(req.name)
    lines = [
        f"开发者: {req.name}",
        f"SAN: {req.san}",
        f"邮箱: {req.email or '-'}",
        f"GitHub: {req.github_url or '-'}",
        f"请求时间: {req.created_at}",
        f"请求文件: {req.path.name if req.path else '-'}",
    ]
    if existing.is_file():
        lines.append("")
        lines.append(f"注意: {existing} 已存在，继续将覆盖并重新签发。")
        if not app.confirm("重新签发证书", lines, danger=True):
            return
    else:
        if not app.confirm("签发开发者证书", lines):
            return

    key = official_key_ui(app, ctx)
    if key is None:
        return
    app.busy("签发证书", f"正在为 {req.name} 签发开发者证书…")
    try:
        cert_path, info = dev_mod.issue_request(ctx.root, req, key)
    except Exception as exc:  # noqa: BLE001
        app.error("签发失败", str(exc))
        return

    # 导出给开发者的证书与回执
    outgoing = ctx.root / "outgoing"
    outgoing.mkdir(exist_ok=True)
    send_cert = outgoing / f"{req.name}.crt"
    atomic_write(send_cert, cert_path.read_bytes())
    atomic_write(
        outgoing / f"{req.name}.receipt.json",
        dumps(_receipt("developer", req.name, info, send_cert)),
    )

    rebuild_and_sign(app, ctx, key)
    log.log_action("developer_issue", f"{req.name} serial={info.serial}")
    app.info(
        "证书已生成",
        [
            f"证书: {cert_path}",
            f"发送给对方: {send_cert}",
            f"序列号: {info.serial}",
            f"指纹: {info.fingerprint}",
            "",
            "请让开发者把证书放到自己仓库的 <developer>/<developer>.crt。",
        ],
    )
    commit_ui(app, ctx, f"Add developer {req.name} (serial {info.serial})")


def sign_domain_wizard(app) -> None:
    ctx = app.ctx
    if not ctx.role.can_issue_domain:
        app.error("只有官方仓库可以签发域证书")
        return
    path = resolve_request_path(app, ctx, kind="domain")
    if path is None:
        return
    try:
        req = dev_mod.load_request(path, ctx.root)
    except Exception as exc:  # noqa: BLE001
        app.error("签名请求无效", str(exc))
        return
    if req.kind != "domain":
        app.error("这不是域签名请求", f"类型为 {req.kind}")
        return
    if not app.confirm(
        "签发域证书",
        [
            f"域名: {req.name}",
            f"SAN: {req.san}",
            f"邮箱: {req.email or '-'}",
            f"索引 URL: {req.index_url or '-'}",
            f"证书 URL: {req.cert_url or '-'}",
            "签发后会自动登记到 domain.yaml（如果 URL 可用）。",
        ],
    ):
        return
    key = official_key_ui(app, ctx)
    if key is None:
        return
    app.busy("签发证书", f"正在为域 {req.name} 签发证书…")
    try:
        cert_path, info = dev_mod.issue_request(ctx.root, req, key)
    except Exception as exc:  # noqa: BLE001
        app.error("签发失败", str(exc))
        return
    atomic_write(
        ctx.root / "outgoing" / f"{req.name}.receipt.json",
        dumps(_receipt("domain", req.name, info, cert_path)),
    )
    log.log_action("domain_issue", f"{req.name} serial={info.serial}")

    index_url, cert_url = req.index_url, req.cert_url
    if not (index_url and cert_url):
        guessed_index, guessed_cert = domains_mod.derive_urls(
            req.repo_url, req.github_url
        )
        index_url = index_url or guessed_index
        cert_url = cert_url or guessed_cert
    if not index_url:
        index_url = app.input(
            "登记域",
            "索引 URL（留空跳过登记）",
            default="",
            hint="用于写入 domain.yaml",
        )
        if index_url is None:
            index_url = ""
    if index_url and not cert_url:
        cert_url = app.input(
            "登记域",
            "证书 URL",
            default="",
            hint="用于写入 domain.yaml",
        )
        if cert_url is None:
            cert_url = ""

    registered = False
    if index_url and cert_url:
        try:
            domains_mod.register_domain(ctx.root, req.name, index_url, cert_url)
            index_mod.rebuild(ctx.root)
            index_mod.sign_index(ctx.root, key)
            registered = True
            log.log_action("domain_register", f"{req.name} {index_url}")
        except Exception as exc:  # noqa: BLE001
            app.error("证书已签发，但登记 domain.yaml 失败", str(exc))
    else:
        app.toast("已签发，但请求中没有 URL，未登记 domain.yaml", "warn", 5)

    app.info(
        "域证书已生成",
        [
            f"证书: {cert_path}",
            f"序列号: {info.serial}",
            f"指纹: {info.fingerprint}",
            f"登记: {'已写入 domain.yaml' if registered else '未登记'}",
            "",
            "把该证书发回域管理员，由对方保存为 official.crt。",
        ],
    )
    message = f"Sign domain {req.name} (serial {info.serial})"
    if registered:
        message += " and register in domain.yaml"
    commit_ui(app, ctx, message)


def revoke_wizard(app) -> None:
    ctx = app.ctx
    developers = dev_mod.list_developers(ctx.root)
    if not developers:
        app.error("没有可吊销的开发者证书")
        return
    options = [
        f"{d.name}  ({d.cert.san[0] if d.cert.san else '-'})"
        for d in developers
    ] + ["手动输入序列号…"]
    index = app.choose("选择要吊销的证书", options)
    if index is None:
        return
    if index < len(developers):
        revoke_developer(app, developers[index])
        return
    serial = app.input(
        "吊销证书", "请输入证书序列号（十六进制）", hint="Esc 取消"
    )
    if not serial:
        return
    serial = serial.strip().upper().removeprefix("0X")
    for developer in developers:
        if developer.cert.serial.upper() == serial:
            revoke_developer(app, developer)
            return
    app.error(f"没有找到序列号为 {serial} 的证书")


def revoke_developer(app, developer: dev_mod.Developer) -> None:
    ctx = app.ctx
    if developer.revoked:
        app.info("该证书已经吊销")
        return
    if developer.cert.san and developer.cert.san[0] == "hpm://official":
        app.error("不能吊销仓库自身的 official 证书")
        return
    if not app.confirm(
        "吊销证书",
        [
            f"开发者: {developer.name}",
            f"序列号: {developer.cert.serial}",
            f"指纹: {developer.cert.fingerprint}",
            "",
            "吊销后将写入 revoke.json 并重新签名。此操作不可撤销。",
        ],
        danger=True,
    ):
        return
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
        serial=developer.cert.serial,
        kind="developer",
        name=developer.name,
        san=developer.cert.san[0] if developer.cert.san else "",
        subject=developer.cert.subject,
        cert_fingerprint=developer.cert.fingerprint,
        reason=reason,
        revoked_by=(app.dev.developer if app.dev else ""),
    )
    revoke.sign(ctx.root, key)
    # 索引也重新签一次，确保发布链一致
    if ctx.index_path.is_file():
        index_mod.sign_index(ctx.root, key)
    log.log_action("revoke", f"{developer.name} {developer.cert.serial} {reason}")
    app.toast(f"已吊销 {developer.name}", "ok", 3)
    commit_ui(
        app,
        ctx,
        f"Revoke {developer.name} certificate (serial {developer.cert.serial})",
    )


def delete_developer_wizard(app) -> None:
    ctx = app.ctx
    developers = dev_mod.list_developers(ctx.root)
    if not developers:
        app.info("没有可删除的开发者")
        return

    options = [
        f"{d.name}  包 {d.packages}  {d.cert.san[0] if d.cert.san else '-'}"
        for d in developers
    ]
    index = app.choose("选择要删除的开发者", options)
    if index is None:
        return
    developer = developers[index]

    if not app.confirm(
        "删除开发者",
        [
            f"开发者: {developer.name}",
            f"目录: {developer.directory}",
            f"包数量: {developer.packages}",
            "",
            "将删除该目录（证书、包）并从索引移除；",
            "私钥仍保留在 ~/.hpm/keys/dev/ 中。",
        ],
        danger=True,
    ):
        return

    mode = app.choose(
        "删除方式",
        ["先吊销证书再删除", "仅删除（证书仍然有效，不推荐）", "取消"],
    )
    if mode is None or mode == 2:
        return

    key = official_key_ui(app, ctx)
    if key is None:
        return

    if mode == 0:
        app.busy("删除开发者", "正在吊销证书…")
        revoke.issue(
            ctx.root,
            serial=developer.cert.serial,
            kind="developer",
            name=developer.name,
            san=developer.cert.san[0] if developer.cert.san else "",
            subject=developer.cert.subject,
            cert_fingerprint=developer.cert.fingerprint,
            reason="ceased",
            revoked_by=(app.dev.developer if app.dev else ""),
            comment="removed by admin",
        )

    app.busy("删除开发者", f"正在删除 {developer.name} …")
    try:
        dev_mod.delete_developer(ctx.root, developer.name)
    except Exception as exc:  # noqa: BLE001
        app.error("删除开发者目录失败", str(exc))
        return

    rebuild_and_sign(app, ctx, key)
    log.log_action("developer_delete", developer.name)
    app.toast(f"已删除开发者 {developer.name}", "ok", 3)
    commit_ui(
        app,
        ctx,
        f"Delete developer {developer.name}" + (" (revoked)" if mode == 0 else ""),
    )
