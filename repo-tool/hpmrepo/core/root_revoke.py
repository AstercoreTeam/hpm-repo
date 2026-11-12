# -*- coding: utf-8 -*-
"""ROOT_revoke.json：根私钥签名的官方证书吊销列表。

该文件不参与普通仓库索引，也不由仓库 official 私钥签名。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from ..util.errors import RepoError
from ..util.fs import atomic_write
from ..util.jsonc import dumps, load_json
from . import crypto

FILENAME = "ROOT_revoke.json"
REASONS = ("keyCompromise", "superseded", "ceased", "unspecified")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_of(root: Path) -> Path:
    return Path(root) / FILENAME


def sig_path_of(root: Path) -> Path:
    return Path(root) / (FILENAME + ".sig")


def empty_doc(root_cert: crypto.CertInfo | None = None) -> dict[str, Any]:
    issuer: dict[str, Any] = {}
    if root_cert is not None:
        issuer = {
            "subject": root_cert.subject,
            "certSha256": root_cert.fingerprint,
        }
    return {
        "schema": 1,
        "updatedAt": now_iso(),
        "issuer": issuer,
        "entries": [],
    }


def load(root: Path) -> dict[str, Any]:
    path = path_of(root)
    if not path.is_file():
        return empty_doc()
    try:
        data = load_json(path)
    except (OSError, ValueError) as exc:
        raise RepoError(f"无法读取 {FILENAME}", str(exc)) from exc
    if not isinstance(data, dict):
        raise RepoError(f"{FILENAME} 根节点必须是对象")
    data.setdefault("schema", 1)
    data.setdefault("updatedAt", now_iso())
    data.setdefault("issuer", {})
    data.setdefault("entries", [])
    return data


def save(root: Path, doc: dict[str, Any]) -> None:
    doc["schema"] = 1
    doc["updatedAt"] = now_iso()
    atomic_write(path_of(root), dumps(doc))


def issue(
    root: Path,
    *,
    serial: str,
    san: str,
    subject: str,
    cert_fingerprint: str,
    reason: str = "unspecified",
    comment: str = "",
    replacement_sha256: str = "",
) -> dict[str, Any]:
    if reason not in REASONS:
        reason = "unspecified"
    doc = load(root)
    serial = serial.upper()
    for entry in doc["entries"]:
        if str(entry.get("serial", "")).upper() == serial:
            return doc
    entry: dict[str, Any] = {
        "serial": serial,
        "san": san,
        "subject": subject,
        "certSha256": cert_fingerprint,
        "revokedAt": now_iso(),
        "reason": reason,
        "comment": comment,
    }
    if replacement_sha256:
        entry["replacementSha256"] = replacement_sha256
    doc["entries"].append(entry)
    save(root, doc)
    return doc


def touch(root: Path, root_cert: crypto.CertInfo | None = None) -> dict[str, Any]:
    doc = load(root)
    if root_cert is not None and not doc.get("issuer"):
        doc["issuer"] = {
            "subject": root_cert.subject,
            "certSha256": root_cert.fingerprint,
        }
    doc["updatedAt"] = now_iso()
    save(root, doc)
    return doc


def sign(root: Path, root_key) -> bytes:
    root = Path(root)
    info = crypto.cert_info(crypto.root_ca())
    doc = load(root)
    issuer = {"subject": info.subject, "certSha256": info.fingerprint}
    if doc.get("issuer") != issuer:
        doc["issuer"] = issuer
        save(root, doc)
    data = path_of(root).read_bytes()
    signature = crypto.sign_bytes(root_key, data)
    atomic_write(sig_path_of(root), signature)
    if not crypto.verify_signature_with_cert(crypto.root_ca(), data, signature):
        raise RepoError("根吊销列表签名自检失败", "根私钥与内置根证书不匹配")
    return signature


def verify(root: Path) -> tuple[bool, str]:
    root = Path(root)
    if not path_of(root).is_file():
        return False, f"{FILENAME} 不存在"
    if not sig_path_of(root).is_file():
        return False, f"{FILENAME}.sig 不存在"
    try:
        ok = crypto.verify_signature_with_cert(
            crypto.root_ca(), path_of(root).read_bytes(), sig_path_of(root).read_bytes()
        )
    except crypto.CryptoError as exc:
        return False, exc.message
    return (True, "签名有效") if ok else (False, "根吊销列表签名无效")


def is_revoked(root: Path, *, serial: str = "", fingerprint: str = "") -> bool:
    try:
        doc = load(root)
    except RepoError:
        return False
    serial = serial.upper()
    for entry in doc.get("entries", []):
        if serial and str(entry.get("serial", "")).upper() == serial:
            return True
        if fingerprint and entry.get("certSha256") == fingerprint:
            return True
    return False
