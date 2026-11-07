# -*- coding: utf-8 -*-
"""revoke.json：证书吊销列表。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from ..util.errors import RepoError
from ..util.fs import atomic_write
from ..util.jsonc import dumps, load_json
from . import crypto

MAX_REASONS = ("keyCompromise", "superseded", "ceased", "unspecified")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_doc(cert: crypto.CertInfo | None = None) -> dict[str, Any]:
    issuer: dict[str, Any] = {}
    if cert is not None:
        issuer = {"san": cert.san[0] if cert.san else "", "certSha256": cert.fingerprint}
    return {
        "schema": 1,
        "updatedAt": now_iso(),
        "issuer": issuer,
        "entries": [],
    }


def load(root: Path) -> dict[str, Any]:
    path = Path(root) / "revoke.json"
    if not path.is_file():
        return empty_doc()
    try:
        data = load_json(path)
    except (OSError, ValueError) as exc:
        raise RepoError(f"无法读取 revoke.json: {path}", str(exc)) from exc
    if not isinstance(data, dict):
        raise RepoError("revoke.json 根节点必须是对象")
    data.setdefault("schema", 1)
    data.setdefault("entries", [])
    data.setdefault("updatedAt", now_iso())
    data.setdefault("issuer", {})
    return data


def save(root: Path, doc: dict[str, Any]) -> None:
    doc["schema"] = 1
    doc["updatedAt"] = now_iso()
    atomic_write(Path(root) / "revoke.json", dumps(doc))


def issue(
    root: Path,
    *,
    serial: str,
    kind: str,
    name: str,
    san: str,
    subject: str,
    cert_fingerprint: str,
    reason: str = "unspecified",
    revoked_by: str = "",
    comment: str = "",
) -> dict[str, Any]:
    if reason not in MAX_REASONS:
        reason = "unspecified"
    doc = load(root)
    serial = serial.upper()
    for entry in doc["entries"]:
        if str(entry.get("serial", "")).upper() == serial:
            return doc
    doc["entries"].append(
        {
            "serial": serial,
            "kind": kind,
            "name": name,
            "san": san,
            "subject": subject,
            "certSha256": cert_fingerprint,
            "revokedAt": now_iso(),
            "reason": reason,
            "revokedBy": revoked_by,
            "comment": comment,
        }
    )
    save(root, doc)
    return doc


def touch(root: Path, cert: crypto.CertInfo | None = None) -> dict[str, Any]:
    """只刷新 updatedAt（必要时补 issuer），不修改任何吊销条目。"""
    doc = load(root)
    if cert is not None and not doc.get("issuer"):
        doc["issuer"] = {
            "san": cert.san[0] if cert.san else "",
            "certSha256": cert.fingerprint,
        }
    doc["updatedAt"] = now_iso()
    save(root, doc)
    return doc


def sign(root: Path, key, cert_path: Path | None = None) -> bytes:
    root = Path(root)
    cert_path = cert_path or (root / "official.crt")
    # 签名前补齐签发者信息，便于客户端判断吊销列表来源
    if cert_path.is_file():
        try:
            cert = crypto.load_cert(cert_path)
            info = crypto.cert_info(cert)
            doc = load(root)
            issuer = {
                "san": info.san[0] if info.san else "",
                "certSha256": info.fingerprint,
            }
            if doc.get("issuer") != issuer:
                doc["issuer"] = issuer
                save(root, doc)
        except crypto.CryptoError:
            pass
    data = (root / "revoke.json").read_bytes()
    signature = crypto.sign_bytes(key, data)
    atomic_write(Path(root) / "revoke.json.sig", signature)
    cert_path = cert_path or (Path(root) / "official.crt")
    if cert_path.is_file():
        cert = crypto.load_cert(cert_path)
        if not crypto.verify_signature_with_cert(cert, data, signature):
            raise RepoError("吊销列表签名自检失败", "私钥与 official.crt 不匹配")
    return signature


def verify(root: Path) -> tuple[bool, str]:
    root = Path(root)
    revoke_path = root / "revoke.json"
    sig_path = root / "revoke.json.sig"
    cert_path = root / "official.crt"
    if not revoke_path.is_file():
        return False, "revoke.json 不存在"
    if not sig_path.is_file():
        return False, "revoke.json.sig 不存在"
    if not cert_path.is_file():
        return False, "official.crt 不存在，无法验签"
    try:
        cert = crypto.load_cert(cert_path)
    except crypto.CryptoError as exc:
        return False, exc.message
    ok = crypto.verify_signature_with_cert(
        cert, revoke_path.read_bytes(), sig_path.read_bytes()
    )
    return (True, "签名有效") if ok else (False, "吊销列表签名无效")


def is_revoked(
    root: Path, *, serial: str | None = None, fingerprint: str | None = None
) -> bool:
    try:
        doc = load(root)
    except RepoError:
        return False
    serial = (serial or "").upper()
    for entry in doc.get("entries", []):
        if serial and str(entry.get("serial", "")).upper() == serial:
            return True
        if fingerprint and entry.get("certSha256") == fingerprint:
            return True
    return False


def entry_for_cert(cert: crypto.CertInfo) -> dict[str, str]:
    return {
        "serial": cert.serial,
        "san": cert.san[0] if cert.san else "",
        "subject": cert.subject,
        "certSha256": cert.fingerprint,
    }
