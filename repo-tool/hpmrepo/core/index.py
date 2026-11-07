# -*- coding: utf-8 -*-
"""index.json 的生成、签名与校验。

索引扫描规则集中在这里实现，工具与私域仓库都不再依赖额外的生成脚本。
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

from ..util.errors import RepoError
from ..util.fs import atomic_write, sha256_file
from ..util.jsonc import dumps, load_json
from . import crypto

try:  # pragma: no cover - 取决于运行环境
    import tomllib as _tomllib
except ImportError:  # pragma: no cover
    try:
        import tomli as _tomllib  # type: ignore
    except ImportError:
        _tomllib = None

IGNORE_DIRS = {
    ".git",
    "publish",
    "__pycache__",
    ".github",
    "toolchains",
    "repo-tool",
    "incoming",
    "requests",
    "outgoing",
    "docs",
}
IGNORE_FILES = {
    "index.json",
    "index.json.sig",
    "README.md",
    "LICENSE",
    "domain.yaml",
    "revoke.json",
    "revoke.json.sig",
}
MANIFEST_NAMES = ("manifest.json", "manifest.toml", "chip.json", "chip.toml")


def _parse_manifest(data: dict[str, Any], is_chip: bool, fallback_chip: str) -> dict[str, Any]:
    chip = data.get("chip", "") or ""
    if is_chip and not chip:
        chip = data.get("name", "") or ""
    if not chip:
        chip = fallback_chip
    return {
        "kind": "chip" if is_chip else "driver",
        "name": data.get("name", "") or "",
        "chip": chip,
        "requires": data.get("requires", {}) or {},
        "description": data.get("description", "") or "",
        "config": data.get("config", {}) or {},
        "provides": data.get("provides", {}) or {},
        "license": data.get("license", "MIT") or "MIT",
        "pins": (data.get("pins", {}) or {}) if is_chip else None,
    }


def _read_package_meta(tar_path: Path, fallback_chip: str = "") -> dict[str, Any] | None:
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                parts = member.name.split("/")
                if len(parts) < 2:
                    continue
                fname = parts[-1]
                if fname not in MANIFEST_NAMES:
                    continue
                extracted = tar.extractfile(member)
                if not extracted:
                    continue
                raw = extracted.read()
                is_chip = fname.startswith("chip")
                if fname.endswith(".toml"):
                    if _tomllib is None:
                        return None
                    data = _tomllib.loads(raw.decode("utf-8"))
                else:
                    data = json.loads(raw.decode("utf-8"))
                return _parse_manifest(data, is_chip, fallback_chip)
    except Exception:  # noqa: BLE001 - 包损坏时跳过
        return None
    return None


def _parse_filename(name: str) -> tuple[str | None, str | None, str | None]:
    from .packages import parse_filename

    return parse_filename(name)


def _load_domains(root: Path) -> dict[str, Any]:
    from .domains import load_domain_config

    return load_domain_config(root)

def build_index(root: Path) -> dict[str, Any]:
    root = Path(root)
    developers: dict[str, Any] = {}
    packages: dict[str, Any] = {}

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in IGNORE_DIRS:
            continue

        dev_name = entry.name
        cert = entry / f"{dev_name}.crt"
        if not cert.is_file():
            continue
        developers[dev_name] = {"cert": f"{{baseurl}}/{dev_name}/{dev_name}.crt"}

        for tar in sorted(entry.glob("*.tar.gz")):
            if tar.name.endswith(".sig"):
                continue
            driver, chip, version = _parse_filename(tar.name)
            if not driver or not version:
                continue
            sig = entry / (tar.name + ".sig")
            if not sig.is_file():
                continue
            meta = _read_package_meta(tar, fallback_chip=chip or "")
            if not meta:
                continue
            sha = sha256_file(tar)
            size = tar.stat().st_size

            if meta["kind"] == "chip":
                package = driver
                chip_final = driver
            else:
                if not chip:
                    continue
                package = f"{driver}-{chip}"
                chip_final = chip

            packages.setdefault(package, {"versions": {}})
            version_entry = {
                "kind": meta["kind"],
                "chip": chip_final,
                "developer": dev_name,
                "url": f"{{baseurl}}/{dev_name}/{tar.name}",
                "sig_url": f"{{baseurl}}/{dev_name}/{tar.name}.sig",
                "sha256": sha,
                "size": size,
                "license": meta["license"],
                "description": meta["description"],
                "config": meta["config"],
                "provides": meta["provides"],
                "requires": meta["requires"],
            }
            if meta["kind"] == "chip" and meta.get("pins") is not None:
                version_entry["pins"] = meta["pins"]
            packages[package]["versions"][version] = version_entry

    return {
        "schema": 2,
        "official_cert": "{baseurl}/official.crt",
        "developers": developers,
        "domains": _load_domains(root),
        "packages": packages,
    }


def rebuild(root: Path, timeout: int = 180) -> tuple[dict[str, Any], str]:
    """重建 index.json 并返回 (索引, 摘要)。"""
    del timeout  # 兼容旧签名
    data = build_index(root)
    atomic_write(Path(root) / "index.json", dumps(data))
    summary = (
        f"开发者: {len(data['developers'])} · "
        f"域: {len(data['domains'])} · "
        f"包: {len(data['packages'])}"
    )
    return data, summary


def load_index(root: Path) -> dict[str, Any]:
    path = Path(root) / "index.json"
    if not path.is_file():
        return {
            "schema": 2,
            "official_cert": "{baseurl}/official.crt",
            "developers": {},
            "domains": {},
            "packages": {},
        }
    try:
        data = load_json(path)
    except (OSError, ValueError) as exc:
        raise RepoError(f"无法读取索引: {path}", str(exc)) from exc
    if not isinstance(data, dict):
        raise RepoError("index.json 根节点必须是对象")
    return data


def package_count(index: dict[str, Any]) -> int:
    return len(index.get("packages") or {})


def developer_count(index: dict[str, Any]) -> int:
    return len(index.get("developers") or {})


def index_bytes(root: Path) -> bytes:
    path = Path(root) / "index.json"
    if not path.is_file():
        raise RepoError(f"index.json 不存在: {path}")
    return path.read_bytes()


def sign_index(root: Path, key, cert_path: Path | None = None) -> bytes:
    data = index_bytes(root)
    signature = crypto.sign_bytes(key, data)
    atomic_write(Path(root) / "index.json.sig", signature)
    cert_path = cert_path or (Path(root) / "official.crt")
    if cert_path.is_file():
        cert = crypto.load_cert(cert_path)
        if not crypto.verify_signature_with_cert(cert, data, signature):
            raise RepoError(
                "索引签名自检失败",
                "私钥与 official.crt 不匹配，或签名算法不一致",
            )
    return signature


def verify_index(root: Path) -> tuple[bool, str]:
    root = Path(root)
    index_path = root / "index.json"
    sig_path = root / "index.json.sig"
    cert_path = root / "official.crt"
    if not index_path.is_file():
        return False, "index.json 不存在"
    if not sig_path.is_file():
        return False, "index.json.sig 不存在"
    if not cert_path.is_file():
        return False, "official.crt 不存在，无法验签"
    try:
        cert = crypto.load_cert(cert_path)
    except crypto.CryptoError as exc:
        return False, exc.message
    ok = crypto.verify_signature_with_cert(
        cert, index_path.read_bytes(), sig_path.read_bytes()
    )
    return (True, "签名有效") if ok else (False, "索引签名无效")


def write_empty_index(root: Path) -> dict[str, Any]:
    data = {
        "schema": 2,
        "official_cert": "{baseurl}/official.crt",
        "developers": {},
        "domains": {},
        "packages": {},
    }
    atomic_write(Path(root) / "index.json", dumps(data))
    return data
