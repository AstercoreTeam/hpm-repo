# -*- coding: utf-8 -*-
"""索引、吊销列表、角色与 Token 测试。"""

from __future__ import annotations

import io
import json
import tarfile

from hpmrepo import config
from hpmrepo.core import crypto, index as index_mod, revoke
from hpmrepo.repo import Role, detect

from pathlib import Path

REPO_SOURCE = Path(__file__).resolve().parents[2]


def _make_package(root, developer: str, name: str = "demo-ch552-1.0.0.tar.gz"):
    path = root / developer / name
    manifest = {
        "name": "demo",
        "version": "1.0.0",
        "chip": "ch552",
        "description": "demo driver",
        "provides": {"methods": []},
        "requires": {},
    }
    with tarfile.open(path, "w:gz") as tar:
        data = json.dumps(manifest).encode()
        info = tarfile.TarInfo(f"{developer}/manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    from hpmrepo.core import crypto

    key = crypto.load_private_key(root / "official.key", None)
    (root / developer / (path.name + ".sig")).write_bytes(
        crypto.sign_bytes(key, path.read_bytes())
    )
    return path


def test_rebuild_and_sign_index(domain_repo, developer_factory):
    developer_factory("alice")
    _make_package(domain_repo, "alice")

    data, _ = index_mod.rebuild(domain_repo)
    assert "demo-ch552" in data["packages"]
    assert "alice" in data["developers"]

    key = crypto.load_private_key(domain_repo / "official.key", None)
    index_mod.sign_index(domain_repo, key)
    ok, detail = index_mod.verify_index(domain_repo)
    assert ok, detail

    # 篡改索引后验签失败
    index_path = domain_repo / "index.json"
    index_path.write_text(index_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    ok, _ = index_mod.verify_index(domain_repo)
    assert not ok


def test_revoke_list(domain_repo, developer_factory):
    _, cert_path, info = developer_factory("alice")
    revoke.issue(
        domain_repo,
        serial=info.serial,
        kind="developer",
        name="alice",
        san=info.san[0],
        subject=info.subject,
        cert_fingerprint=info.fingerprint,
        reason="superseded",
    )
    assert revoke.is_revoked(domain_repo, serial=info.serial)
    assert revoke.is_revoked(domain_repo, fingerprint=info.fingerprint)

    key = crypto.load_private_key(domain_repo / "official.key", None)
    revoke.sign(domain_repo, key)
    ok, detail = revoke.verify(domain_repo)
    assert ok, detail

    # 重复写入同一序列号不会产生第二条
    revoke.issue(
        domain_repo,
        serial=info.serial,
        kind="developer",
        name="alice",
        san=info.san[0],
        subject=info.subject,
        cert_fingerprint=info.fingerprint,
        reason="ceased",
    )
    doc = revoke.load(domain_repo)
    assert len(doc["entries"]) == 1


def test_role_detection(repo, ca, monkeypatch):
    assert detect(repo).role is Role.UNINITIALIZED

    # 官方证书
    (repo / "official.crt").write_bytes((REPO_SOURCE / "official.crt").read_bytes())
    assert detect(repo).role is Role.OFFICIAL

    # 非根签发的“官方”证书
    _, _, ca_pem = ca
    (repo / "official.crt").write_bytes(ca_pem)
    assert detect(repo).role is Role.INVALID_CERT

    # 域证书（由测试 CA 签发）
    ca_key, ca_cert, ca_pem = ca
    issuer = repo / "issuer.crt"
    issuer.write_bytes(ca_pem)
    monkeypatch.setenv("HPM_OFFICIAL_CERT", str(issuer))
    from hpmrepo.core import crypto
    from cryptography.hazmat.primitives.asymmetric import ed25519

    domain_key = ed25519.Ed25519PrivateKey.generate()
    (repo / "official.crt").write_bytes(
        crypto.issue_certificate(
            ca_cert,
            ca_key,
            domain_key.public_key(),
            "HPM Domain test",
            "hpm://domain/test",
            profile="domain",
        )
    )
    ctx = detect(repo)
    assert ctx.role is Role.PRIVATE
    assert ctx.domain == "test"

    # 证书损坏
    (repo / "official.crt").write_text("not a cert", encoding="utf-8")
    assert detect(repo).role is Role.INVALID_CERT


def test_token_states(repo, tmp_path, monkeypatch):
    home = tmp_path / "hpm-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HPM_HOME", str(home))
    assert config.token_status(config.load_dev()).state == "missing"

    (home / "dev.json").write_text(
        json.dumps({"accessToken": "x", "expiresAt": "2999-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    assert config.token_status(config.load_dev()).state == "valid"

    (home / "dev.json").write_text(
        json.dumps({"accessToken": "x", "expiresAt": "2000-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    assert config.token_status(config.load_dev()).state == "expired"

    (home / "dev.json").write_text(
        json.dumps({"accessToken": ""}), encoding="utf-8"
    )
    assert config.token_status(config.load_dev()).state == "missing"


def test_builder_matches_existing_index():
    from hpmrepo.core.index import build_index

    data = build_index(REPO_SOURCE)
    existing = json.loads((REPO_SOURCE / "index.json").read_text(encoding="utf-8"))
    assert data == existing


def test_current_repo_passes_publish_gate():
    from hpmrepo.core.verify import publish_gate
    from hpmrepo.repo import detect

    ctx = detect(REPO_SOURCE)
    assert ctx.role.value == "official"
    assert publish_gate(ctx) == []
