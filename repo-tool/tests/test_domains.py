# -*- coding: utf-8 -*-
"""私域列表与密钥目录布局测试。"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from hpmrepo.core import crypto, domains, keys


def test_key_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("HPM_HOME", str(tmp_path / "hpm"))
    assert keys.official_key_path("example") == (
        tmp_path / "hpm" / "keys" / "domain" / "example" / "official.key"
    )
    assert keys.official_key_path("") == tmp_path / "hpm" / "keys" / "official.key"
    assert keys.developer_key_path("alice") == (
        tmp_path / "hpm" / "keys" / "dev" / "alice" / "alice.key"
    )


def test_domain_list_excludes_developer_cert(repo, ca):
    ca_key, ca_cert, _ = ca
    outgoing = repo / "outgoing"
    outgoing.mkdir()

    dev_key = ed25519.Ed25519PrivateKey.generate()
    (outgoing / "alice.crt").write_bytes(
        crypto.issue_certificate(
            ca_cert,
            ca_key,
            dev_key.public_key(),
            "HPM Developer alice",
            "hpm://developer/alice",
            profile="developer",
        )
    )
    domain_key = ed25519.Ed25519PrivateKey.generate()
    (outgoing / "example.crt").write_bytes(
        crypto.issue_certificate(
            ca_cert,
            ca_key,
            domain_key.public_key(),
            "HPM Domain example",
            "hpm://domain/example",
            profile="domain",
        )
    )
    entries = domains.list_domains(repo)
    assert [e.name for e in entries] == ["example"]
    assert entries[0].cert is not None
    assert entries[0].status == "已签发，未登记"


def test_register_domain_and_derive_urls(repo):
    index, cert = domains.derive_urls("https://github.com/alice/hpm-repo")
    assert index == "https://raw.githubusercontent.com/alice/hpm-repo/main/index.json"
    assert cert == "https://raw.githubusercontent.com/alice/hpm-repo/main/official.crt"

    path = domains.register_domain(repo, "alice", index, cert)
    assert path.is_file()
    config = domains.load_domain_config(repo)
    assert config["alice"]["index"] == index
    assert config["alice"]["cert"] == cert
    # 第二次登记其他域时保留已有域
    domains.register_domain(
        repo, "bob", "https://example.com/index.json", "https://example.com/official.crt"
    )
    config = domains.load_domain_config(repo)
    assert set(config) == {"alice", "bob"}


def test_remove_domain_removes_config_and_files(repo, ca):
    ca_key, ca_cert, _ = ca
    outgoing = repo / "outgoing"
    outgoing.mkdir()
    domains.register_domain(
        repo, "alice", "https://example.com/index.json", "https://example.com/official.crt"
    )
    domains.register_domain(
        repo, "bob", "https://example.org/index.json", "https://example.org/official.crt"
    )
    key = ed25519.Ed25519PrivateKey.generate()
    (outgoing / "alice.crt").write_bytes(
        crypto.issue_certificate(
            ca_cert,
            ca_key,
            key.public_key(),
            "HPM Domain alice",
            "hpm://domain/alice",
            profile="domain",
        )
    )
    (outgoing / "alice.receipt.json").write_text("{}", encoding="utf-8")

    removed = domains.remove_domain(repo, "alice")
    config = domains.load_domain_config(repo)
    assert set(config) == {"bob"}
    assert not (outgoing / "alice.crt").exists()
    assert not (outgoing / "alice.receipt.json").exists()
    assert repo / "domain.yaml" in removed
