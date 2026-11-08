# -*- coding: utf-8 -*-
"""签名请求的生成、校验与证书签发。"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util.errors import RequestInvalid
from ..util.fs import atomic_write, ensure_private
from ..util.jsonc import canonical_json, dumps
from . import crypto

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
RESERVED_NAMES = {
    "repo-tool",
    "publish",
    "incoming",
    "requests",
    "outgoing",
    "tests",
    "docs",
    "toolchains",
    "official",
}
REQUEST_GLOB = "*.request.json"


@dataclass
class Request:
    kind: str
    name: str
    san: str
    csr_pem: str
    public_key_pem: str
    email: str
    github_url: str
    created_at: str
    nonce: str
    repo_url: str = ""
    index_url: str = ""
    cert_url: str = ""
    path: Path | None = None
    raw: dict[str, Any] | None = None

    @property
    def label(self) -> str:
        return f"{self.kind} · {self.name}"


@dataclass
class PendingRequest:
    path: Path
    kind: str = ""
    name: str = ""
    created_at: str = ""
    valid: bool = False
    error: str = ""

    @property
    def label(self) -> str:
        kind = {"developer": "开发者", "domain": "私域"}.get(self.kind, self.kind or "?")
        name = self.name or self.path.name
        state = "" if self.valid else f"  [{self.error or '无效'}]"
        return f"[{kind}] {name}  {self.created_at or ''}{state}"


@dataclass
class Developer:
    name: str
    directory: Path
    cert: crypto.CertInfo
    revoked: bool
    key_present: bool
    packages: int = 0


def _iso(dt_value: dt.datetime) -> str:
    return dt_value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _san_for(kind: str, name: str) -> str:
    return f"hpm://developer/{name}" if kind == "developer" else f"hpm://domain/{name}"


def _common_name(kind: str, name: str) -> str:
    prefix = "HPM Developer" if kind == "developer" else "HPM Domain"
    return f"{prefix} {name}"


def build_request(
    key,
    *,
    kind: str,
    name: str,
    email: str = "",
    github_url: str = "",
    repo_url: str = "",
    index_url: str = "",
    cert_url: str = "",
) -> dict[str, Any]:
    if kind not in ("developer", "domain"):
        raise RequestInvalid(f"未知请求类型: {kind}")
    if not NAME_RE.match(name):
        raise RequestInvalid("名称只能包含小写字母、数字、点、下划线、短横线")
    san = _san_for(kind, name)
    csr_pem = crypto.create_csr(key, _common_name(kind, name), san).decode()
    public_pem = crypto.public_key_pem(key).decode()
    payload: dict[str, Any] = {
        "schema": 1,
        "kind": kind,
        "name": name,
        "san": san,
        "publicKey": public_pem,
        "csr": csr_pem,
        "email": email,
        "githubUrl": github_url,
        "createdAt": _iso(dt.datetime.now(dt.timezone.utc)),
        "nonce": base64.b64encode(secrets.token_bytes(16)).decode(),
    }
    if repo_url:
        payload["repoUrl"] = repo_url
    if index_url:
        payload["indexUrl"] = index_url
    if cert_url:
        payload["certUrl"] = cert_url
    signature = crypto.sign_bytes(key, canonical_json(payload))
    doc = dict(payload)
    doc["selfSignature"] = base64.b64encode(signature).decode()
    return doc


def write_request(root: Path, doc: dict[str, Any], stem: str = "official") -> tuple[Path, Path]:
    root = Path(root)
    json_path = root / f"{stem}.request.json"
    txt_path = root / f"{stem}.request.txt"
    atomic_write(json_path, dumps(doc))
    csr = doc.get("csr", "")
    txt = (
        "HPM 仓库证书签名请求\n"
        "====================\n\n"
        f"类型: {doc.get('kind')}\n"
        f"名称: {doc.get('name')}\n"
        f"SAN : {doc.get('san')}\n"
        f"时间: {doc.get('createdAt')}\n"
        f"邮箱: {doc.get('email') or '-'}\n\n"
        "请把同目录下的 .request.json 文件发送给官方仓库维护者，\n"
        "由对方在 hrm 的「管理开发者 → 签发域证书」中签名，\n"
        "并把返回的 official.crt 放到本仓库根目录。\n\n"
        "----- 公钥 -----\n"
        f"{doc.get('publicKey', '').strip()}\n\n"
        "----- CSR -----\n"
        f"{csr.strip()}\n"
    )
    atomic_write(txt_path, txt)
    return json_path, txt_path


def _decode_b64(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise RequestInvalid(f"{field} 不是合法 base64") from exc


def validate_request_data(data: dict[str, Any], root: Path | None = None) -> Request:
    if not isinstance(data, dict):
        raise RequestInvalid("请求必须是 JSON 对象")
    if int(data.get("schema", 0)) != 1:
        raise RequestInvalid("不支持的请求版本 schema")

    kind = str(data.get("kind", ""))
    if kind not in ("developer", "domain"):
        raise RequestInvalid(f"未知请求类型: {kind or '空'}")

    name = str(data.get("name", ""))
    if not NAME_RE.match(name):
        raise RequestInvalid(
            "名称不合法", "只能使用小写字母、数字、点、下划线、短横线，且以字母或数字开头"
        )
    if name in RESERVED_NAMES:
        raise RequestInvalid(f"名称被保留: {name}")

    san = str(data.get("san", ""))
    expected = _san_for(kind, name)
    if san != expected:
        raise RequestInvalid("SAN 与名称不匹配", f"期望 {expected}，实际 {san or '空'}")

    csr_pem = str(data.get("csr", ""))
    public_pem = str(data.get("publicKey", ""))
    if not csr_pem.startswith("-----BEGIN CERTIFICATE REQUEST-----"):
        raise RequestInvalid("缺少 CSR")
    if not public_pem.startswith("-----BEGIN PUBLIC KEY-----"):
        raise RequestInvalid("缺少公钥")

    try:
        csr = crypto.load_csr_bytes(csr_pem.encode())
    except crypto.CryptoError as exc:
        raise RequestInvalid(exc.message) from exc
    if not csr.is_signature_valid:
        raise RequestInvalid("CSR 自签名无效")

    try:
        pub = crypto.load_public_key(public_pem.encode())
    except crypto.CryptoError as exc:
        raise RequestInvalid(exc.message) from exc

    csr_pub_pem = crypto.public_key_pem(csr.public_key())
    if csr_pub_pem != crypto.public_key_pem(pub):
        raise RequestInvalid("CSR 公钥与请求公钥不一致")

    csr_san = crypto.csr_info(csr).san
    if csr_san and san not in csr_san:
        raise RequestInvalid("CSR SAN 与请求 SAN 不一致")

    self_sig = str(data.get("selfSignature", ""))
    if not self_sig:
        raise RequestInvalid("缺少自签名")
    sig_bytes = _decode_b64(self_sig, "selfSignature")
    payload = {k: v for k, v in data.items() if k != "selfSignature"}
    if not crypto.verify_bytes(pub, canonical_json(payload), sig_bytes):
        raise RequestInvalid("请求自签名无效")

    created = _parse_iso(str(data.get("createdAt", "")))
    if created is None:
        raise RequestInvalid("createdAt 时间格式错误")
    now = dt.datetime.now(dt.timezone.utc)
    if created > now + dt.timedelta(days=1):
        raise RequestInvalid("请求时间在未来")
    if now - created > dt.timedelta(days=30):
        raise RequestInvalid("请求已超过 30 天")

    nonce = str(data.get("nonce", ""))
    if len(nonce) < 8:
        raise RequestInvalid("nonce 过短")
    if root is not None and _nonce_exists(Path(root), nonce):
        raise RequestInvalid("该请求已处理过", "nonce 重复")

    return Request(
        kind=kind,
        name=name,
        san=san,
        csr_pem=csr_pem,
        public_key_pem=public_pem,
        email=str(data.get("email") or ""),
        github_url=str(data.get("githubUrl") or ""),
        created_at=str(data.get("createdAt") or ""),
        nonce=nonce,
        repo_url=str(data.get("repoUrl") or ""),
        index_url=str(data.get("indexUrl") or ""),
        cert_url=str(data.get("certUrl") or ""),
        raw=data,
    )


def _nonce_exists(root: Path, nonce: str) -> bool:
    requests_dir = root / "requests"
    if not requests_dir.is_dir():
        return False
    for path in requests_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("nonce") == nonce:
            return True
    return False


def load_request(path: Path, root: Path | None = None) -> Request:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RequestInvalid(f"无法读取请求: {path}", str(exc)) from exc
    req = validate_request_data(data, root)
    req.path = Path(path)
    return req


def issue_request(
    root: Path,
    req: Request,
    ca_key,
    *,
    days: int | None = None,
) -> tuple[Path, crypto.CertInfo]:
    root = Path(root)
    ca_cert_path = root / "official.crt"
    if not ca_cert_path.is_file():
        raise RequestInvalid("仓库缺少 official.crt，无法签发")
    ca_cert = crypto.load_cert(ca_cert_path)
    info = crypto.cert_info(ca_cert)
    if not info.is_ca:
        raise RequestInvalid("official.crt 不是 CA 证书，不能签发")
    if req.kind == "domain" and not (info.san and info.san[0] == "hpm://official"):
        raise RequestInvalid("只有官方仓库可以签发域证书")

    csr = crypto.load_csr_bytes(req.csr_pem.encode())
    pem = crypto.issue_certificate_from_csr(
        ca_cert,
        ca_key,
        csr,
        _common_name(req.kind, req.name),
        req.san,
        profile=req.kind,
        days=days,
    )
    cert = crypto.load_cert_bytes(pem)
    cert_info = crypto.cert_info(cert)
    cert_info.san = cert_info.san or [req.san]

    if req.kind == "developer":
        target_dir = root / req.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{req.name}.crt"
        # 开发者中心同步需要公钥与公钥签名
        pub_pem = crypto.public_key_pem(csr.public_key())
        atomic_write(target_dir / f"{req.name}.pub", pub_pem, mode=0o644)
        atomic_write(
            target_dir / f"{req.name}.pub.sig",
            crypto.sign_bytes(ca_key, pub_pem),
            mode=0o644,
        )
    else:
        outgoing = root / "outgoing"
        outgoing.mkdir(exist_ok=True)
        target = outgoing / f"{req.name}.crt"
    atomic_write(target, pem)

    if req.raw is not None:
        archive_dir = root / "requests"
        archive_dir.mkdir(exist_ok=True)
        atomic_write(
            archive_dir / f"{req.name}-{cert_info.serial}.request.json",
            dumps(req.raw),
        )
    if req.path is not None:
        from ..repo import request_inbox

        for directory in (Path(root) / "incoming", request_inbox()):
            try:
                if directory.is_dir() and req.path.resolve().parent == directory.resolve():
                    req.path.unlink()
                    # 同时删除同名 .txt 人读版本，避免请求目录残留
                    txt = req.path.with_name(
                        req.path.name.replace(".request.json", ".request.txt")
                    )
                    if txt.is_file():
                        txt.unlink()
                    break
            except OSError:
                continue
    return target, cert_info


def _request_dirs(root: Path) -> list[Path]:
    from ..repo import request_inbox

    dirs = [request_inbox(), Path(root) / "incoming"]
    seen: set[Path] = set()
    result: list[Path] = []
    for directory in dirs:
        try:
            resolved = directory.resolve()
        except OSError:
            resolved = directory
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(directory)
    return result


def list_requests(root: Path) -> list[Path]:
    """待处理请求路径：优先 repo-tool/requests/，兼容旧 incoming/。"""
    paths: list[Path] = []
    seen: set[Path] = set()
    for directory in _request_dirs(Path(root)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(REQUEST_GLOB)):
            try:
                key = path.resolve()
            except OSError:
                key = path
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def list_request_summaries(root: Path) -> list[PendingRequest]:
    """读取请求列表，供管理员选择；不因单个文件损坏而中断。"""
    summaries: list[PendingRequest] = []
    for path in list_requests(root):
        summary = PendingRequest(path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            summary.error = f"无法读取: {exc}"
            summaries.append(summary)
            continue
        if isinstance(data, dict):
            summary.kind = str(data.get("kind") or "")
            summary.name = str(data.get("name") or "")
            summary.created_at = str(data.get("createdAt") or "")
        try:
            req = validate_request_data(data, Path(root))
            summary.valid = True
            summary.kind = req.kind
            summary.name = req.name
            summary.created_at = req.created_at
        except (RequestInvalid, ValueError, TypeError) as exc:
            summary.error = getattr(exc, "message", str(exc))
        summaries.append(summary)
    return summaries


def list_developers(root: Path) -> list[Developer]:
    from . import revoke

    root = Path(root)
    developers: list[Developer] = []
    if not root.is_dir():
        return developers
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in {
            "repo-tool",
            "publish",
            "incoming",
            "requests",
            "outgoing",
            "tests",
        }:
            continue
        cert_path = entry / f"{entry.name}.crt"
        if not cert_path.is_file():
            continue
        try:
            info = crypto.cert_info(crypto.load_cert(cert_path))
        except crypto.CryptoError:
            continue
        packages = len(list(entry.glob("*.tar.gz")))
        developers.append(
            Developer(
                name=entry.name,
                directory=entry,
                cert=info,
                revoked=revoke.is_revoked(
                    root, serial=info.serial, fingerprint=info.fingerprint
                ),
                key_present=(entry / f"{entry.name}.key").is_file(),
                packages=packages,
            )
        )
    return developers


def save_generated_key(path: Path, private_pem: bytes, public_pem: bytes) -> None:
    atomic_write(path, private_pem, mode=0o600)
    ensure_private(path)
    atomic_write(path.with_suffix(".pub"), public_pem, mode=0o644)


def delete_developer(root: Path, name: str) -> Path:
    """删除开发者目录（证书、包），索引由调用方重建。"""
    root = Path(root).resolve()
    if not NAME_RE.match(name) or name in RESERVED_NAMES:
        raise RequestInvalid(f"开发者名不合法: {name}")
    path = (root / name)
    if path.is_symlink():
        raise RequestInvalid(f"拒绝删除符号链接: {path}")
    resolved = path.resolve()
    if resolved.parent != root:
        raise RequestInvalid(f"路径越界: {path}")
    if not resolved.is_dir():
        raise RequestInvalid(f"开发者目录不存在: {path}")
    shutil.rmtree(resolved)
    return resolved
