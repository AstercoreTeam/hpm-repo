# -*- coding: utf-8 -*-
"""域列表：读取 domain.yaml 与 outgoing 中已签发的域证书。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..util.errors import RepoError
from ..util.fs import atomic_write
from . import crypto, revoke


@dataclass
class DomainEntry:
    name: str
    index: str = ""
    cert_url: str = ""
    local_cert: Path | None = None
    cert: crypto.CertInfo | None = None
    registered: bool = True
    revoked: bool = False

    @property
    def status(self) -> str:
        if self.revoked:
            return "证书已吊销"
        if not self.local_cert:
            return "仅登记，无本地证书"
        return "已登记" if self.registered else "已签发，未登记"


def load_domain_config(root: Path) -> dict[str, dict[str, str]]:
    path = Path(root) / "domain.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RepoError("需要 PyYAML 才能读取 domain.yaml", "pip3 install pyyaml") from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise RepoError("读取 domain.yaml 失败", str(exc)) from exc
    domains = data.get("domains", {})
    if not isinstance(domains, dict):
        raise RepoError("domain.yaml 的 domains 字段必须是对象")
    result: dict[str, dict[str, str]] = {}
    for name, info in domains.items():
        if not isinstance(info, dict):
            continue
        index = str(info.get("index", "") or "")
        cert = str(info.get("cert", "") or "")
        if index and cert:
            result[str(name)] = {"index": index, "cert": cert}
    return result


def list_domains(root: Path) -> list[DomainEntry]:
    root = Path(root)
    config = load_domain_config(root)
    outgoing = root / "outgoing"
    entries: list[DomainEntry] = []
    seen: set[str] = set()

    for name, info in sorted(config.items()):
        local = outgoing / f"{name}.crt"
        entry = DomainEntry(
            name=name,
            index=info.get("index", ""),
            cert_url=info.get("cert", ""),
            local_cert=local if local.is_file() else None,
        )
        if entry.local_cert is not None:
            try:
                entry.cert = crypto.cert_info(crypto.load_cert(entry.local_cert))
                entry.revoked = revoke.is_revoked(
                    root,
                    serial=entry.cert.serial,
                    fingerprint=entry.cert.fingerprint,
                )
            except crypto.CryptoError:
                entry.cert = None
        entries.append(entry)
        seen.add(name)

    if outgoing.is_dir():
        for cert_path in sorted(outgoing.glob("*.crt")):
            name = cert_path.stem
            if name in seen:
                continue
            try:
                info = crypto.cert_info(crypto.load_cert(cert_path))
            except crypto.CryptoError:
                continue
            # outgoing 同时会放开发者证书，只有域证书才进入私域列表
            if not any(san.startswith("hpm://domain/") for san in info.san):
                continue
            entry = DomainEntry(
                name=name, local_cert=cert_path, registered=False, cert=info
            )
            entry.revoked = revoke.is_revoked(
                root,
                serial=entry.cert.serial,
                fingerprint=entry.cert.fingerprint,
            )
            entries.append(entry)
    return entries


def pending_domain_requests(root: Path) -> int:
    from . import developers

    return sum(
        1
        for summary in developers.list_request_summaries(root)
        if summary.kind == "domain"
    )


def derive_urls(repo_url: str, github_url: str = "") -> tuple[str, str]:
    """从仓库地址推导 (index.json, official.crt) 的 URL。"""
    url = (repo_url or "").strip().rstrip("/")
    if not url and github_url:
        url = github_url.strip().rstrip("/") + "/hpm-repo"
    if not url:
        return "", ""
    if url.endswith("index.json"):
        return url, url[: -len("index.json")] + "official.crt"
    if url.endswith("official.crt"):
        return url[: -len("official.crt")] + "index.json", url
    if "github.com/" in url and "raw.githubusercontent.com" not in url:
        rest = url.split("github.com/", 1)[1].strip("/")
        parts = rest.split("/")
        if len(parts) >= 2:
            base = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/main"
            return base + "/index.json", base + "/official.crt"
    if "raw.githubusercontent.com/" in url:
        return url + "/index.json", url + "/official.crt"
    return url + "/index.json", url + "/official.crt"


def register_domain(root: Path, name: str, index_url: str, cert_url: str) -> Path:
    """把域写入 domain.yaml（保留其他域与顶层字段）。"""
    name = (name or "").strip()
    index_url = (index_url or "").strip()
    cert_url = (cert_url or "").strip()
    if not name:
        raise RepoError("域名不能为空")
    if not index_url.startswith(("http://", "https://")):
        raise RepoError("索引 URL 必须以 http:// 或 https:// 开头")
    if not cert_url.startswith(("http://", "https://")):
        raise RepoError("证书 URL 必须以 http:// 或 https:// 开头")
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RepoError("需要 PyYAML 才能写入 domain.yaml", "pip3 install pyyaml") from exc

    path = Path(root) / "domain.yaml"
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise RepoError("读取 domain.yaml 失败", str(exc)) from exc
    else:
        data = {}
    if not isinstance(data, dict):
        raise RepoError("domain.yaml 根节点必须是对象")
    domains = data.setdefault("domains", {})
    if not isinstance(domains, dict):
        raise RepoError("domain.yaml 的 domains 字段必须是对象")
    domains[name] = {"index": index_url, "cert": cert_url}
    atomic_write(
        path,
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        mode=0o644,
    )
    return path


def remove_domain(root: Path, name: str) -> list[Path]:
    """从 domain.yaml 删除域，并移除 outgoing 中的证书/回执。"""
    root = Path(root)
    name = (name or "").strip()
    if not name:
        raise RepoError("域名不能为空")
    removed: list[Path] = []

    for suffix in (".crt", ".receipt.json"):
        path = root / "outgoing" / f"{name}{suffix}"
        if path.is_file():
            path.unlink()
            removed.append(path)

    path = root / "domain.yaml"
    if path.is_file():
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RepoError("需要 PyYAML 才能修改 domain.yaml", "pip3 install pyyaml") from exc
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise RepoError("读取 domain.yaml 失败", str(exc)) from exc
        if not isinstance(data, dict):
            raise RepoError("domain.yaml 根节点必须是对象")
        domains = data.get("domains", {})
        if isinstance(domains, dict) and name in domains:
            domains.pop(name)
            atomic_write(
                path,
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                mode=0o644,
            )
            removed.append(path)
    return removed
