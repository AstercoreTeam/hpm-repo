# -*- coding: utf-8 -*-
"""仓库定位与角色判定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .core import crypto
from .util.errors import RepoError


class Role(str, Enum):
    OFFICIAL = "official"
    PRIVATE = "private"
    PRIVATE_UNVERIFIED = "private_unverified"
    UNINITIALIZED = "uninitialized"
    INVALID_CERT = "invalid_cert"

    @property
    def label(self) -> str:
        return {
            Role.OFFICIAL: "官方仓库",
            Role.PRIVATE: "私域仓库",
            Role.PRIVATE_UNVERIFIED: "私域仓库（未验证签发者）",
            Role.UNINITIALIZED: "未初始化",
            Role.INVALID_CERT: "证书异常",
        }[self]

    @property
    def needs_login(self) -> bool:
        return self in (Role.PRIVATE, Role.PRIVATE_UNVERIFIED, Role.UNINITIALIZED)

    @property
    def can_manage(self) -> bool:
        return self in (
            Role.OFFICIAL,
            Role.PRIVATE,
            Role.PRIVATE_UNVERIFIED,
        )

    @property
    def can_issue_domain(self) -> bool:
        return self is Role.OFFICIAL


def repo_root() -> Path:
    """仓库根 = repo-tool 的上一级；测试可用 HPM_REPO_ROOT 覆盖。"""
    env = os.environ.get("HPM_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "repo-tool":
            return parent.parent
    return Path.cwd().resolve()


@dataclass
class RepoContext:
    root: Path
    role: Role
    cert: crypto.CertInfo | None = None
    domain: str = ""
    cert_error: str = ""

    @property
    def official_cert_path(self) -> Path:
        return self.root / "official.crt"

    @property
    def official_key_path(self) -> Path:
        return self.root / "official.key"

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    @property
    def index_sig_path(self) -> Path:
        return self.root / "index.json.sig"

    @property
    def revoke_path(self) -> Path:
        return self.root / "revoke.json"

    @property
    def revoke_sig_path(self) -> Path:
        return self.root / "revoke.json.sig"

    @property
    def incoming_dir(self) -> Path:
        return self.root / "incoming"

    @property
    def requests_dir(self) -> Path:
        return self.root / "requests"

    def developer_dir(self, name: str) -> Path:
        return self.root / name

    def developer_cert(self, name: str) -> Path:
        return self.developer_dir(name) / f"{name}.crt"

    def developer_key(self, name: str) -> Path:
        return self.developer_dir(name) / f"{name}.key"


def _extract_domain(sans: list[str]) -> str:
    for san in sans:
        if san.startswith("hpm://domain/"):
            return san[len("hpm://domain/") :]
    return ""


def detect(root: Path | None = None) -> RepoContext:
    root = (root or repo_root()).resolve()
    cert_path = root / "official.crt"
    if not cert_path.is_file():
        return RepoContext(root=root, role=Role.UNINITIALIZED)

    try:
        cert = crypto.load_cert(cert_path)
        info = crypto.cert_info(cert)
    except crypto.CryptoError as exc:
        return RepoContext(root=root, role=Role.INVALID_CERT, cert_error=exc.message)

    sans = info.san
    if sans == ["hpm://official"]:
        try:
            if crypto.verify_cert_signed_by(cert, crypto.root_ca()):
                return RepoContext(root=root, role=Role.OFFICIAL, cert=info)
        except crypto.CryptoError:
            pass
        return RepoContext(
            root=root,
            role=Role.INVALID_CERT,
            cert=info,
            cert_error="官方证书不是由 HPM Root CA 签发",
        )

    domain = _extract_domain(sans)
    if domain and len(sans) == 1:
        issuer = crypto.official_issuer_cert(root)
        if issuer is None:
            return RepoContext(
                root=root,
                role=Role.PRIVATE_UNVERIFIED,
                cert=info,
                domain=domain,
            )
        if crypto.verify_cert_signed_by(cert, issuer):
            return RepoContext(
                root=root, role=Role.PRIVATE, cert=info, domain=domain
            )
        return RepoContext(
            root=root,
            role=Role.PRIVATE_UNVERIFIED,
            cert=info,
            domain=domain,
            cert_error="私域证书无法用本机官方证书验证（可能官方证书已轮换）",
        )

    return RepoContext(
        root=root,
        role=Role.INVALID_CERT,
        cert=info,
        cert_error=f"SAN 不符合仓库证书要求: {', '.join(sans) or '无 SAN'}",
    )


def require_writable(root: Path) -> None:
    if not os.access(root, os.W_OK):
        raise RepoError(f"仓库目录不可写: {root}")
