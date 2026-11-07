# -*- coding: utf-8 -*-
"""私钥定位：只使用用户家目录下的 .hpm（支持 HPM_HOME 覆盖）。

约定：
    ~/.hpm/keys/official.key                        官方仓库私钥
    ~/.hpm/keys/domain/<domain>/official.key        域私钥
    ~/.hpm/keys/dev/<developer>/<developer>.key     开发者私钥

旧布局（keys/<name>/...）仍作为兼容回退读取。

仓库目录中不再查找或保存私钥；找不到时由界面提示用户输入路径。
"""

from __future__ import annotations

from pathlib import Path

from ..config import hpm_home
from . import crypto


def keys_dir() -> Path:
    return hpm_home() / "keys"


def official_key_path(domain: str = "") -> Path:
    """域私钥：keys/domain/<domain>/official.key；官方仓库：keys/official.key。"""
    domain = (domain or "").strip()
    if domain:
        return keys_dir() / "domain" / domain / "official.key"
    return keys_dir() / "official.key"


def developer_key_path(developer: str) -> Path:
    """开发者私钥：keys/dev/<developer>/<developer>.key。"""
    return keys_dir() / "dev" / developer / f"{developer}.key"


def _dedupe(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path not in result:
            result.append(path)
    return result


def official_candidates(domain: str = "") -> list[Path]:
    """新目录优先，同时兼容旧目录，避免用户已有密钥“找不到”。"""
    paths: list[Path] = []
    if domain:
        paths.append(keys_dir() / "domain" / domain / "official.key")
        paths.append(keys_dir() / domain / "official.key")  # 旧布局
    paths.append(keys_dir() / "official.key")
    paths.append(hpm_home() / "official.key")
    if not domain:
        # 官方仓库/未初始化时，扫描所有域目录
        paths.extend(sorted((keys_dir() / "domain").glob("*/official.key")))
        paths.extend(sorted(keys_dir().glob("*/official.key")))
    return _dedupe(paths)


def developer_candidates(developer: str) -> list[Path]:
    paths = [
        keys_dir() / "dev" / developer / f"{developer}.key",
        keys_dir() / developer / f"{developer}.key",  # 旧布局
        hpm_home() / f"{developer}.key",
    ]
    paths.extend(sorted((keys_dir() / "dev").glob(f"*/{developer}.key")))
    paths.extend(sorted(keys_dir().glob(f"*/{developer}.key")))
    return _dedupe(paths)


def find_official(domain: str = "", cert_path: Path | None = None) -> Path | None:
    """查找 official 私钥。

    若提供 official.crt，优先返回与其公钥匹配的私钥，避免同时存在多个键时
    用错私钥（并因此误报“私钥未加密”）。匹配顺序：
      1. 同名 .pub 与证书公钥一致；
      2. 私钥未加密时直接导出公钥比较；
      3. 都无法验证时，优先返回加密私钥（没有匹配结论时不返回不确定的明文私钥）。
    """
    existing = [p for p in official_candidates(domain) if p.is_file()]
    if not existing:
        return None
    if cert_path is None or not Path(cert_path).is_file():
        return existing[0]

    try:
        cert_pub = crypto.public_key_pem(crypto.load_cert(cert_path).public_key())
    except crypto.CryptoError:
        return existing[0]

    encrypted: list[Path] = []
    for path in existing:
        pub_path = path.with_suffix(".pub")
        if pub_path.is_file():
            try:
                pub = crypto.load_public_key(pub_path.read_bytes())
                if crypto.public_key_pem(pub) == cert_pub:
                    return path
            except crypto.CryptoError:
                pass

        if crypto.private_key_is_encrypted(path):
            encrypted.append(path)
            continue

        try:
            key = crypto.load_private_key(path, None)
        except crypto.CryptoError:
            continue
        if crypto.public_key_pem(key) == cert_pub:
            return path

    if encrypted:
        return encrypted[0]
    # 无法确认匹配时也返回一个候选，避免界面误报“未找到”；
    # 真正的密钥/证书不匹配会在签名自检时给出明确错误。
    return existing[0]

def find_developer(developer: str) -> Path | None:
    for path in developer_candidates(developer):
        if path.is_file():
            return path
    return None


def key_looks_valid(path: Path) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "PRIVATE KEY" in text


def key_is_encrypted(path: Path) -> bool:
    return crypto.private_key_is_encrypted(path)
