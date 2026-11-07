# -*- coding: utf-8 -*-
"""生成签名请求：私域仓库（domain）与开发者（developer）。

未登录用户也可以使用；请求写入 repo-tool/requests/，由官方仓库管理员读取并签发。
"""

from __future__ import annotations

from pathlib import Path

from ..core import crypto, developers as dev_mod, domains as domains_mod, keys as keys_mod
from ..tui.widgets import MenuItem
from ..util import log
from ..util.fs import ensure_private
from .base import MenuView, View
from .common import ask_password_pair, load_key_with_password


class RequestView(MenuView):
    title = "生成签名请求"
    subtitle = "无需登录 · 提交到 requests/"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.refresh_items()

    def build_items(self) -> list[MenuItem]:
        return [
            MenuItem("私域仓库请求", "申请 hpm://domain/<name>", action="domain"),
            MenuItem("开发者请求", "申请 hpm://developer/<name>", action="developer"),
            MenuItem("← 返回", "", action="back"),
        ]

    def header_lines(self) -> list[str]:
        return [
            f"请求目录: {self.app.ctx.inbox_dir}",
            f"仓库: {self.app.ctx.root}",
            "",
            "生成后把 .request.json 交给官方仓库管理员；管理员用本工具签发。",
            "私钥只保存在本机，不会写入请求文件。",
        ]

    def on_select(self, item: MenuItem) -> None:
        if item.action == "back":
            self.app.pop()
        elif item.action in ("domain", "developer"):
            if generate_request_wizard(self.app, item.action):
                self.refresh_items()


def _default_name(app, kind: str) -> str:
    dev = app.dev
    if kind == "domain" and app.ctx.domain:
        return app.ctx.domain
    if dev and dev.developer:
        return dev.developer
    return ""


def _default_key_path(app, kind: str, name: str) -> Path:
    if kind == "domain":
        return keys_mod.official_key_path(name)
    return keys_mod.developer_key_path(name)


def generate_request_wizard(app, kind: str) -> bool:
    if kind not in ("domain", "developer"):
        app.error(f"未知请求类型: {kind}")
        return False

    label = "私域仓库" if kind == "domain" else "开发者"
    default_name = _default_name(app, kind)
    name = app.input(
        f"生成{label}请求",
        f"{label}名称（小写字母、数字、点、下划线、短横线）",
        default=default_name,
        hint="会写入证书 SAN",
    )
    if name is None:
        return False
    name = name.strip()
    if not dev_mod.NAME_RE.match(name) or name in dev_mod.RESERVED_NAMES:
        app.error(
            "名称不合法",
            "只能使用小写字母、数字、点、下划线、短横线；部分名称被保留。",
        )
        return False

    email = app.input(
        f"生成{label}请求", "联系邮箱（可留空）", default="", hint="Esc 取消"
    )
    if email is None:
        return False
    github = app.input(
        f"生成{label}请求",
        "GitHub 地址（可留空）",
        default=(app.dev.github_url if app.dev else ""),
        hint="Esc 取消",
    )
    if github is None:
        return False

    repo_url = ""
    index_url = ""
    cert_url = ""
    if kind == "domain":
        default_repo = ""
        if app.dev and app.dev.repo_url:
            default_repo = app.dev.repo_url
        elif github:
            default_repo = github.rstrip("/") + "/hpm-repo"
        repo_url = app.input(
            "生成私域仓库请求",
            "私域仓库地址（用于签发后自动登记 domain.yaml，可留空）",
            default=default_repo,
            hint="Esc 取消",
        )
        if repo_url is None:
            return False
        repo_url = repo_url.strip()
        index_url, cert_url = domains_mod.derive_urls(repo_url, github)

    mode = app.choose(
        "私钥",
        ["生成新的 Ed25519 私钥", "使用已有私钥", "取消"],
        hint="签名请求需要私钥对应的公钥",
    )
    if mode is None or mode == 2:
        return False

    if mode == 0:
        key = _generate_key(app, kind, name)
        if key is None:
            return False
        key_path = Path(key[1])
        key_obj = key[0]
    else:
        default_path = _default_key_path(app, kind, name)
        text = app.input(
            "使用已有私钥",
            "请输入私钥路径（PEM）",
            default=str(default_path),
            hint="Esc 取消",
        )
        if text is None:
            return False
        key_path = Path(text).expanduser()
        if not key_path.is_file():
            app.error(f"私钥不存在: {key_path}")
            return False
        if not keys_mod.key_looks_valid(key_path):
            app.error("文件看起来不是 PEM 私钥", str(key_path))
            return False
        key_obj = load_key_with_password(app, key_path, name)
        if key_obj is None:
            return False

    try:
        doc = dev_mod.build_request(
            key_obj,
            kind=kind,
            name=name,
            email=email,
            github_url=github,
            repo_url=repo_url,
            index_url=index_url,
            cert_url=cert_url,
        )
        json_path, txt_path = dev_mod.write_request(
            app.ctx.inbox_dir, doc, stem=f"{kind}-{name}"
        )
    except Exception as exc:  # noqa: BLE001
        app.error("生成签名请求失败", str(exc))
        return False

    log.log_action("request_generate", f"{kind} {name} -> {json_path}")
    app.info(
        "签名请求已生成",
        [
            f"请求文件: {json_path}",
            f"人读版本: {txt_path}",
            f"私钥: {key_path}",
            *(
                [f"索引 URL: {index_url}", f"证书 URL: {cert_url}"]
                if kind == "domain" and index_url
                else []
            ),
            "",
            "下一步：",
            "1. 把 .request.json 交给官方仓库管理员；",
            "2. 管理员在 hrm 中签发证书；",
            "3. 收到证书后，私钥用于签名包或初始化仓库。",
            "",
            "请妥善保管私钥，不要发送给任何人。",
        ],
    )
    app.toast("请求已写入 requests/", "ok", 3)
    return True


def _generate_key(app, kind: str, name: str):
    """生成新密钥到 ~/.hpm/keys，返回 (key_obj, path)。"""
    path = _default_key_path(app, kind, name)
    if path.exists():
        choice = app.choose(
            "私钥已存在",
            ["使用已有私钥", "覆盖生成新私钥", "取消"],
            hint=f"{path}",
        )
        if choice is None or choice == 2:
            return None
        if choice == 0:
            key = load_key_with_password(app, path, name)
            return (key, path) if key is not None else None

    password = ask_password_pair(
        app, "私钥密码", "请为新的 Ed25519 私钥设置密码（至少 8 位）"
    )
    if password is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        private_pem, public_pem = crypto.generate_ed25519_keypair(password)
        dev_mod.save_generated_key(path, private_pem, public_pem)
        ensure_private(path)
        key = crypto.load_private_key(path, password)
    except Exception as exc:  # noqa: BLE001
        app.error("生成私钥失败", str(exc))
        return None
    app.toast(f"私钥已保存: {path}", "ok", 3)
    return key, path
