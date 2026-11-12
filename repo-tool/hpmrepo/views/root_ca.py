# -*- coding: utf-8 -*-
"""信任根管理：签发/吊销官方证书、更新 ROOT 吊销列表日期。

根私钥每次操作都重新询问路径和密码，不进入 App 的密钥缓存。
"""

from __future__ import annotations

from pathlib import Path

from ..core import crypto, root_revoke
from ..repo import Role
from ..tui.screen import Screen, ellipsize, st
from ..tui.widgets import MenuItem, draw_footer, draw_title
from ..util import log
from ..util.fs import atomic_write
from .base import MenuView, View
from .common import commit_ui, official_key_ui

ROOT_REASONS = {
    "密钥泄露 / 私钥丢失（keyCompromise）": "keyCompromise",
    "证书被替换 / 重新签发（superseded）": "superseded",
    "停止使用（ceased）": "ceased",
    "其他 / 未指定（unspecified）": "unspecified",
}


def _load_root_key(app):
    """每次操作都提示根私钥路径；不写入 app 密钥缓存。"""
    text = app.input(
        "根私钥",
        "请输入 HPM 根私钥路径（每次操作都需重新指定，不缓存）",
        default="",
        hint="PEM 或 OpenSSH 私钥 · Esc 取消",
    )
    if text is None or not text.strip():
        return None
    path = Path(text).expanduser()
    if not path.is_file():
        app.error(f"根私钥不存在: {path}")
        return None
    if not crypto.private_key_is_encrypted(path):
        try:
            return crypto.load_private_key(path, None)
        except crypto.CryptoError as exc:
            app.error("无法读取根私钥", exc.message)
            return None
    for attempt in range(3):
        password = app.input(
            "根私钥密码",
            "根私钥已加密，请输入密码",
            mask=True,
            hint=f"第 {attempt + 1}/3 次 · 不使用密钥缓存 · Esc 取消",
        )
        if password is None:
            return None
        try:
            return crypto.load_private_key(path, password)
        except crypto.PasswordError:
            continue
        except crypto.CryptoError as exc:
            app.error("无法读取根私钥", exc.message)
            return None
    app.error("根私钥密码错误次数过多")
    return None


class RootManagementView(MenuView):
    title = "信任根管理"
    subtitle = "官方证书签发 / 根吊销列表"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.refresh_items()

    def build_items(self) -> list[MenuItem]:
        return [
            MenuItem("签发官方证书", "用根私钥签 official.crt", action="issue"),
            MenuItem("吊销官方证书", "写入 ROOT_revoke.json", action="revoke"),
            MenuItem("更新 ROOT 吊销列表日期", "仅刷新 updatedAt 并重签", action="touch"),
            MenuItem("← 返回", "", action="back"),
        ]

    def header_lines(self) -> list[str]:
        ctx = self.app.ctx
        lines = [
            f"仓库: {ctx.root}",
            f"根证书: {crypto.fingerprint(crypto.root_ca())}",
        ]
        if ctx.cert:
            lines.append(
                f"官方证书: {ctx.cert.serial} · 剩余 {ctx.cert.days_left} 天"
            )
        ok, detail = root_revoke.verify(ctx.root) if root_revoke.path_of(ctx.root).is_file() else (False, "不存在")
        lines.append(f"ROOT_revoke.json: {detail}")
        lines.append("根私钥不会缓存，每次操作需重新输入路径。")
        return lines

    def on_select(self, item: MenuItem) -> None:
        if item.action == "back":
            self.app.pop()
        elif item.action == "issue":
            issue_official_cert(self.app)
        elif item.action == "revoke":
            revoke_official_cert(self.app)
        elif item.action == "touch":
            touch_root_revoke(self.app)
        self.refresh_items()


def _require_official(app) -> bool:
    if app.ctx.role is not Role.OFFICIAL:
        app.error("当前不是官方仓库", "该操作只允许在官方仓库执行。")
        return False
    if app.ctx.cert is None:
        app.error("缺少 official.crt", "请先完成官方仓库证书初始化。")
        return False
    return True


def issue_official_cert(app) -> None:
    if not _require_official(app):
        return
    ctx = app.ctx
    cert_path = ctx.official_cert_path
    if cert_path.exists():
        if not app.confirm(
            "重新签发官方证书",
            [
                f"现有证书: {ctx.cert.subject if ctx.cert else '-'}",
                f"序列号: {ctx.cert.serial if ctx.cert else '-'}",
                "",
                "将覆盖 official.crt（旧文件备份为 official.crt.bak）。",
                "建议先吊销旧官方证书，或把旧序列号加入 ROOT_revoke.json。",
            ],
            danger=True,
        ):
            return

    official_key = official_key_ui(app, ctx)
    if official_key is None:
        return
    root_key = _load_root_key(app)
    if root_key is None:
        return

    app.busy("签发官方证书", "正在用根私钥签发 official.crt …")
    try:
        csr = crypto.load_csr_bytes(
            crypto.create_csr(official_key, "HPM Official Registry", "hpm://official")
        )
        root_cert = crypto.root_ca()
        pem = crypto.issue_certificate(
            root_cert,
            root_key,
            csr.public_key(),
            "HPM Official Registry",
            "hpm://official",
            profile="official",
            days=1825,
        )
        new_cert = crypto.load_cert_bytes(pem)
        if not crypto.verify_cert_signed_by(new_cert, root_cert):
            raise crypto.CryptoError("新官方证书无法用根证书验证")
        if cert_path.is_file():
            atomic_write(ctx.root / "official.crt.bak", cert_path.read_bytes())
        atomic_write(cert_path, pem)
        app.reload()
    except Exception as exc:  # noqa: BLE001
        app.error("签发官方证书失败", str(exc))
        return

    info = crypto.cert_info(new_cert)
    log.log_action("root_issue_official", f"serial={info.serial}")
    app.info(
        "官方证书已签发",
        [
            f"证书: {cert_path}",
            f"序列号: {info.serial}",
            f"指纹: {info.fingerprint}",
            f"有效期至: {info.not_after.astimezone().strftime('%Y-%m-%d %H:%M')}",
            "",
            "根私钥未缓存；请把新证书同步到官方仓库与镜像。",
        ],
    )
    commit_ui(app, ctx, "Reissue official.crt")


def revoke_official_cert(app) -> None:
    if not _require_official(app):
        return
    ctx = app.ctx
    cert = ctx.cert
    assert cert is not None
    if not app.confirm(
        "吊销官方证书",
        [
            f"主题: {cert.subject}",
            f"序列号: {cert.serial}",
            f"指纹: {cert.fingerprint}",
            "",
            "将写入 ROOT_revoke.json 并用根私钥签名。",
            "客户端需要支持根吊销列表才会拒绝该证书。",
        ],
        danger=True,
    ):
        return
    labels = list(ROOT_REASONS.keys())
    choice = app.choose("吊销原因", labels)
    if choice is None:
        return
    reason = ROOT_REASONS[labels[choice]]
    root_key = _load_root_key(app)
    if root_key is None:
        return

    app.busy("吊销官方证书", "正在写入 ROOT_revoke.json …")
    try:
        root_revoke.issue(
            ctx.root,
            serial=cert.serial,
            san=cert.san[0] if cert.san else "hpm://official",
            subject=cert.subject,
            cert_fingerprint=cert.fingerprint,
            reason=reason,
            comment="revoked by official admin",
        )
        root_revoke.sign(ctx.root, root_key)
    except Exception as exc:  # noqa: BLE001
        app.error("吊销官方证书失败", str(exc))
        return
    ok, detail = root_revoke.verify(ctx.root)
    if not ok:
        app.error("根吊销列表校验失败", detail)
        return
    log.log_action("root_revoke_official", f"serial={cert.serial}")
    app.info(
        "官方证书已吊销",
        [
            f"证书: {ctx.official_cert_path}",
            f"序列号: {cert.serial}",
            f"ROOT_revoke.json: {root_revoke.path_of(ctx.root)}",
            "",
            "请把 ROOT_revoke.json 与 .sig 分发到官方仓库/镜像。",
        ],
    )
    commit_ui(app, ctx, "Revoke official certificate")


def touch_root_revoke(app) -> None:
    if app.ctx.role is not Role.OFFICIAL:
        app.error("当前不是官方仓库", "该操作只允许在官方仓库执行。")
        return
    ctx = app.ctx
    root_key = _load_root_key(app)
    if root_key is None:
        return
    app.busy("更新根吊销列表", "正在刷新日期并重新签名…")
    try:
        if not root_revoke.path_of(ctx.root).is_file():
            root_revoke.save(
                ctx.root,
                root_revoke.empty_doc(crypto.cert_info(crypto.root_ca())),
            )
        root_revoke.touch(ctx.root, crypto.cert_info(crypto.root_ca()))
        root_revoke.sign(ctx.root, root_key)
    except Exception as exc:  # noqa: BLE001
        app.error("更新根吊销列表失败", str(exc))
        return
    ok, detail = root_revoke.verify(ctx.root)
    if not ok:
        app.error("根吊销列表校验失败", detail)
        return
    log.log_action("root_revoke_touch", "updatedAt")
    app.toast("ROOT 吊销列表日期已更新", "ok", 3)
    commit_ui(app, ctx, "Refresh ROOT_revoke.json date")
