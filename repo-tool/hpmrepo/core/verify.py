# -*- coding: utf-8 -*-
"""仓库校验门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import gitops, index as index_mod, packages, revoke
from ..repo import RepoContext, Role


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    warn: bool = False

    @property
    def mark(self) -> str:
        if self.ok:
            return "OK"
        return "!!" if not self.warn else "??"


def run_all(ctx: RepoContext, *, deep: bool = True) -> list[Check]:
    root = ctx.root
    checks: list[Check] = []

    checks.append(
        Check(
            "仓库角色",
            ctx.role in (Role.OFFICIAL, Role.PRIVATE, Role.PRIVATE_UNVERIFIED),
            f"{ctx.role.label}"
            + (f"（{ctx.domain}）" if ctx.domain else "")
            + (f"：{ctx.cert_error}" if ctx.cert_error else ""),
        )
    )

    if ctx.cert is not None:
        checks.append(
            Check(
                "仓库证书",
                not ctx.cert.expired,
                f"{ctx.cert.subject} · 剩余 {ctx.cert.days_left} 天",
            )
        )

    ok, detail = index_mod.verify_index(root)
    checks.append(Check("索引签名", ok, detail))

    if (root / "revoke.json").is_file():
        ok, detail = revoke.verify(root)
        checks.append(Check("吊销列表签名", ok, detail))
    else:
        checks.append(Check("吊销列表", True, "尚未生成 revoke.json", warn=True))

    if deep:
        bad = []
        total = 0
        for item in packages.verify_all(root):
            total += 1
            if not item.ok:
                bad.append(f"{item.package}@{item.version}: " + "；".join(item.issues or []))
        checks.append(
            Check(
                "包完整性",
                not bad,
                f"{total} 个版本全部通过" if not bad else f"{len(bad)} 个版本异常",
            )
        )
        if bad:
            for line in bad[:5]:
                checks.append(Check("", False, line))

    if gitops.available() and gitops.is_repo(root):
        status = gitops.status(root)
        checks.append(
            Check(
                "Git",
                True,
                f"{status.branch or '-'} · 变更 {status.dirty} · ↑{status.ahead} ↓{status.behind}",
            )
        )
        keys: list[Path] = []
        if ctx.official_key_path.is_file():
            keys.append(ctx.official_key_path)
        for dev_dir in root.iterdir():
            if dev_dir.is_dir() and (dev_dir / f"{dev_dir.name}.key").is_file():
                keys.append(dev_dir / f"{dev_dir.name}.key")
        leaked = [
            str(k.relative_to(root))
            for k in keys
            if not gitops.is_ignored(root, str(k.relative_to(root)))
        ]
        checks.append(
            Check(
                "私钥忽略规则",
                not leaked,
                "未发现会被提交的私钥" if not leaked else "以下私钥未被 .gitignore 忽略: " + ", ".join(leaked),
            )
        )
    else:
        checks.append(Check("Git", True, "未初始化 Git 仓库", warn=True))

    return checks


def publish_gate(ctx: RepoContext) -> list[str]:
    """签名/推送前的硬性失败项。"""
    failures: list[str] = []
    if ctx.role in (Role.UNINITIALIZED, Role.INVALID_CERT):
        failures.append(f"仓库角色异常：{ctx.role.label}")
    ok, detail = index_mod.verify_index(ctx.root)
    if not ok:
        failures.append(f"索引校验失败：{detail}")
    for item in packages.verify_all(ctx.root):
        if not item.ok:
            failures.append(
                f"{item.package}@{item.version}：" + "；".join(item.issues or [])
            )
    for dev in _developers(ctx):
        if dev.revoked:
            failures.append(f"开发者 {dev.name} 的证书已被吊销")
    return failures


def _developers(ctx: RepoContext):
    from . import developers as dev_mod

    return dev_mod.list_developers(ctx.root)
