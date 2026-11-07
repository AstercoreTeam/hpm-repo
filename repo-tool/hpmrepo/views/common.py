# -*- coding: utf-8 -*-
"""视图层共用：密钥选择、密码输入、重建索引、Git 提交。"""

from __future__ import annotations

import os
from pathlib import Path

from ..core import crypto, gitops, index as index_mod, keys as keys_mod, revoke
from ..repo import RepoContext
from ..util import log
from ..util.fs import atomic_write, ensure_private


def _load_key(app, path: Path, label: str):
    cached = app.cached_key(str(path))
    if cached is not None:
        return cached

    encrypted = crypto.private_key_is_encrypted(path)
    env_password = os.environ.get("HPM_KEY_PASSWORD")
    if encrypted and env_password:
        try:
            key = crypto.load_private_key(path, env_password)
            app.cache_key(str(path), key)
            return key
        except crypto.PasswordError:
            pass
    if not encrypted:
        if os.environ.get("HPM_ALLOW_UNENCRYPTED") != "1":
            if app.confirm(
                "私钥未加密",
                [
                    f"{label} 的私钥没有密码保护。",
                    "现在设置密码并加密保存吗？",
                    "",
                    "选择「取消」将继续使用未加密私钥。",
                ],
                danger=True,
            ):
                password = ask_password_pair(
                    app,
                    "设置私钥密码",
                    f"{label} 的私钥未加密，请设置新密码（至少 8 位）",
                )
                if password is None:
                    return None
                key = crypto.load_private_key(path, None)
                atomic_write(
                    path, crypto.encrypt_private_key_pem(key, password), mode=0o600
                )
                ensure_private(path)
                app.cache_key(str(path), key)
                return key
        try:
            key = crypto.load_private_key(path, None)
        except crypto.CryptoError:
            raise
        ensure_private(path)
        app.cache_key(str(path), key)
        return key

    for attempt in range(3):
        password = app.input(
            "私钥密码",
            f"{label} 已加密，请输入密码",
            mask=True,
            hint=f"第 {attempt + 1}/3 次 · Esc 取消",
        )
        if password is None:
            return None
        try:
            key = crypto.load_private_key(path, password)
        except crypto.PasswordError:
            continue
        ensure_private(path)
        app.cache_key(str(path), key)
        return key
    app.error("密码错误次数过多", "请确认私钥密码后重试。")
    return None


def load_key_with_password(app, path: Path, label: str):
    """供新视图复用的私钥加载入口（密码重试 + 会话缓存）。"""
    return _load_key(app, path, label)


def ask_password_pair(
    app, title: str = "私钥密码", prompt: str = "请为私钥设置密码（至少 8 位）"
) -> str | None:
    """生成新私钥时必须设置密码：两次输入，至少 8 位。"""
    for _ in range(3):
        first = app.input(
            title,
            prompt,
            mask=True,
            hint="密码只保存在内存，不写入磁盘 · Esc 取消",
        )
        if first is None:
            return None
        if len(first) < 8:
            app.error("密码过短", "私钥密码至少需要 8 位。")
            continue
        second = app.input("确认密码", "请再次输入密码", mask=True, hint="Esc 取消")
        if second is None:
            return None
        if first != second:
            app.error("两次输入的密码不一致")
            continue
        return first
    return None


def _ask_key_path(app, title: str, default: Path) -> Path | None:
    text = app.input(
        title,
        "请输入私钥路径（PEM）",
        default=str(default),
        hint="Tab 不可用，请直接输入完整路径 · Esc 取消",
    )
    if text is None:
        return None
    path = Path(text).expanduser()
    if not path.is_file():
        app.error(f"私钥不存在: {path}")
        return None
    if not keys_mod.key_looks_valid(path):
        app.error("文件看起来不是 PEM 私钥", f"{path}")
        return None
    return path


def official_key_ui(app, ctx: RepoContext):
    path = keys_mod.find_official(ctx.domain, ctx.official_cert_path)
    if path is None:
        path = _ask_key_path(app, "官方私钥", keys_mod.official_key_path(ctx.domain))
        if path is None:
            return None
    return _load_key(app, path, "official")


def developer_key_ui(app, ctx: RepoContext, developer: str):
    path = keys_mod.find_developer(developer)
    if path is None:
        path = _ask_key_path(
            app, f"{developer} 的私钥", keys_mod.developer_key_path(developer)
        )
        if path is None:
            return None
    return _load_key(app, path, developer)


def rebuild_and_sign(app, ctx: RepoContext, official_key) -> dict:
    app.busy("更新索引", "正在扫描仓库并生成 index.json …")
    data, _output = index_mod.rebuild(ctx.root)
    index_mod.sign_index(ctx.root, official_key)
    if ctx.revoke_path.is_file():
        revoke.sign(ctx.root, official_key)
    log.log_action("index", f"rebuilt and signed in {ctx.root}")
    return data


def sign_existing(app, ctx: RepoContext, official_key) -> None:
    index_mod.sign_index(ctx.root, official_key)
    if ctx.revoke_path.is_file():
        revoke.sign(ctx.root, official_key)


def commit_ui(
    app,
    ctx: RepoContext,
    message: str,
    *,
    paths: list[str] | None = None,
    ask_push: bool = True,
) -> bool:
    if not (gitops.available() and gitops.is_repo(ctx.root)):
        app.toast("当前目录不是 Git 仓库，跳过提交", "warn")
        return False

    if paths is None:
        paths = gitops.changed_paths(ctx.root)
    paths = [p for p in paths if p]
    key_paths = [p for p in paths if p.endswith(".key")]
    if key_paths:
        app.error("拒绝提交私钥", "以下文件是私钥：" + ", ".join(key_paths))
        return False
    if not paths:
        app.toast("没有需要提交的变更", "info", 2.5)
        return False

    preview = "\n".join(paths[:10]) + ("\n…" if len(paths) > 10 else "")
    if not app.confirm("提交 Git", [f"提交信息：{message}", "", preview]):
        return False

    app.busy("Git", "正在提交…")
    gitops.add(ctx.root, paths)
    result = gitops.commit(ctx.root, message)
    if not result.ok:
        app.error("git commit 失败", result.summary())
        return False
    log.log_action("git", f"commit {message}")

    if ask_push and gitops.remote_url(ctx.root):
        if app.confirm("推送远程", [f"远程：{gitops.remote_url(ctx.root)}", "现在推送到远程仓库？"]):
            app.busy("Git", "正在推送…")
            status = gitops.status(ctx.root)
            branch = status.branch if status.branch and status.branch != "HEAD" else ""
            pushed = gitops.push(ctx.root, "origin", branch)
            if not pushed.ok:
                app.error("推送失败", pushed.summary())
                return False
            app.toast("已推送", "ok")
    app.toast("已提交", "ok", 2.5)
    return True


def resolve_request_path(
    app, ctx: RepoContext, kind: str | None = None
) -> Path | None:
    from ..core import developers

    summaries = developers.list_request_summaries(ctx.root)
    if kind is not None:
        summaries = [s for s in summaries if s.kind == kind]
    if not summaries:
        label = {"developer": "开发者", "domain": "域"}.get(kind or "", "")
        app.info(
            f"没有待签发的{label}请求" if label else "没有待签发的请求",
            [
                f"请求目录: {ctx.inbox_dir}",
                "请把 .request.json 放到该目录后重新打开此页面。",
            ],
        )
        return None
    options = [s.label for s in summaries]
    index = app.choose("选择签名请求", options, hint="↑↓ 选择 · Enter 确认 · Esc 取消")
    if index is None:
        return None
    return summaries[index].path
