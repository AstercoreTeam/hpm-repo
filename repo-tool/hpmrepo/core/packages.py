# -*- coding: utf-8 -*-
"""包扫描、签名、入库与删除。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util.errors import RepoError
from ..util.fs import atomic_write, sha256_file
from . import crypto, index as index_mod, revoke


@dataclass
class PackageVersion:
    package: str
    version: str
    kind: str
    chip: str
    developer: str
    url: str
    sig_url: str
    sha256: str
    size: int
    license: str
    description: str
    tar_path: Path | None = None
    sig_path: Path | None = None
    installed: bool = False
    hash_ok: bool = False
    sig_ok: bool = False
    revoked: bool = False
    issues: list[str] | None = None

    @property
    def ok(self) -> bool:
        return self.installed and self.hash_ok and self.sig_ok and not self.revoked


def parse_filename(name: str) -> tuple[str | None, str | None, str | None]:
    """<driver>-<chip>-<version>.tar.gz / <chip>-<version>.tar.gz"""
    if not name.endswith(".tar.gz"):
        return None, None, None
    base = name[: -len(".tar.gz")]
    parts = base.rsplit("-", 2)
    if len(parts) == 3:
        driver, chip, version = parts
    elif len(parts) == 2:
        driver, version = parts
        chip = ""
    else:
        return None, None, None
    for part in version.split("."):
        if not part.isdigit():
            return None, None, None
    return driver, chip, version


def index_packages(root: Path) -> list[dict[str, Any]]:
    data = index_mod.load_index(root)
    packages = data.get("packages") or {}
    rows: list[dict[str, Any]] = []
    for name, pkg in packages.items():
        for version, entry in (pkg.get("versions") or {}).items():
            rows.append({"package": name, "version": version, **entry})
    rows.sort(key=lambda r: (r["package"], r["version"]))
    return rows


def verify_version(root: Path, entry: dict[str, Any]) -> PackageVersion:
    root = Path(root)
    developer = str(entry.get("developer") or "")
    url = str(entry.get("url") or "")
    sig_url = str(entry.get("sig_url") or "")
    tar_name = url.rsplit("/", 1)[-1]
    sig_name = sig_url.rsplit("/", 1)[-1]
    tar_path = root / developer / tar_name if developer else None
    sig_path = root / developer / sig_name if developer else None
    issues: list[str] = []

    installed = bool(tar_path and tar_path.is_file())
    hash_ok = False
    size = int(entry.get("size") or 0)
    if installed:
        actual_size = tar_path.stat().st_size
        if actual_size != size:
            issues.append(f"大小不符（期望 {size}，实际 {actual_size}）")
        digest = sha256_file(tar_path)
        expected = str(entry.get("sha256") or "")
        hash_ok = digest == expected
        if not hash_ok:
            issues.append("sha256 不匹配")
    else:
        issues.append("tar.gz 不存在")

    sig_ok = False
    cert_path = root / developer / f"{developer}.crt" if developer else None
    if sig_path and sig_path.is_file() and tar_path and tar_path.is_file() and cert_path and cert_path.is_file():
        try:
            cert = crypto.load_cert(cert_path)
            sig_ok = crypto.verify_signature_with_cert(
                cert, tar_path.read_bytes(), sig_path.read_bytes()
            )
        except crypto.CryptoError:
            sig_ok = False
        if not sig_ok:
            issues.append("包签名无效")
    else:
        issues.append("缺少签名或开发者证书")

    revoked_flag = False
    if cert_path and cert_path.is_file():
        try:
            info = crypto.cert_info(crypto.load_cert(cert_path))
            revoked_flag = revoke.is_revoked(
                root, serial=info.serial, fingerprint=info.fingerprint
            )
            if revoked_flag:
                issues.append("开发者证书已吊销")
        except crypto.CryptoError:
            pass

    return PackageVersion(
        package=str(entry.get("package") or ""),
        version=str(entry.get("version") or ""),
        kind=str(entry.get("kind") or ""),
        chip=str(entry.get("chip") or ""),
        developer=developer,
        url=url,
        sig_url=sig_url,
        sha256=str(entry.get("sha256") or ""),
        size=size,
        license=str(entry.get("license") or ""),
        description=str(entry.get("description") or ""),
        tar_path=tar_path,
        sig_path=sig_path,
        installed=installed,
        hash_ok=hash_ok,
        sig_ok=sig_ok,
        revoked=revoked_flag,
        issues=issues,
    )


def verify_all(root: Path) -> list[PackageVersion]:
    return [verify_version(root, e) for e in index_packages(root)]


def sign_file(key, path: Path, sig_path: Path | None = None) -> Path:
    path = Path(path)
    if not path.is_file():
        raise RepoError(f"文件不存在: {path}")
    sig_path = sig_path or path.with_name(path.name + ".sig")
    signature = crypto.sign_bytes(key, path.read_bytes())
    atomic_write(sig_path, signature)
    return sig_path


def install_package(root: Path, tar: Path, sig: Path, developer: str) -> tuple[Path, Path]:
    dest_dir = Path(root) / developer
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_tar = dest_dir / Path(tar).name
    dest_sig = dest_dir / Path(sig).name
    try:
        if Path(tar).resolve() != dest_tar.resolve():
            shutil.copy2(tar, dest_tar)
        if Path(sig).resolve() != dest_sig.resolve():
            shutil.copy2(sig, dest_sig)
    except OSError as exc:
        raise RepoError("复制包到仓库失败", str(exc)) from exc
    return dest_tar, dest_sig


def remove_version(root: Path, package: str, version: str) -> list[Path]:
    data = index_mod.load_index(root)
    pkg = (data.get("packages") or {}).get(package)
    if not pkg:
        raise RepoError(f"索引中没有包: {package}")
    entry = (pkg.get("versions") or {}).get(version)
    if not entry:
        raise RepoError(f"索引中没有版本: {package}@{version}")
    removed: list[Path] = []
    developer = str(entry.get("developer") or "")
    for key in ("url", "sig_url"):
        filename = str(entry.get(key) or "").rsplit("/", 1)[-1]
        if not filename or not developer:
            continue
        path = Path(root) / developer / filename
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def default_tarball(root: Path) -> Path | None:
    root = Path(root)
    candidates: list[Path] = []
    for directory in (root / "publish", root):
        if directory.is_dir():
            candidates.extend(directory.glob("*.tar.gz"))
    candidates = [p for p in candidates if not p.name.endswith(".sig")]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def resolve_cert(root: Path, developer: str) -> Path | None:
    path = Path(root) / developer / f"{developer}.crt"
    return path if path.is_file() else None
