# -*- coding: utf-8 -*-
"""根吊销列表与官方证书 profile 测试。"""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from hpmrepo.core import crypto, root_revoke


def _root():
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "HPM Test Root"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HPM"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(9)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=2), critical=True)
        .sign(key, None)
    )
    return key, cert


def test_official_certificate_profile(tmp_path, monkeypatch):
    root_key, root_cert = _root()
    monkeypatch.setattr(crypto, "root_ca", lambda: root_cert)
    official_key = ed25519.Ed25519PrivateKey.generate()
    pem = crypto.issue_certificate(
        root_cert,
        root_key,
        official_key.public_key(),
        "HPM Official Registry",
        "hpm://official",
        profile="official",
        days=1825,
    )
    info = crypto.cert_info(crypto.load_cert_bytes(pem))
    assert info.is_ca
    assert info.san == ["hpm://official"]
    issued = crypto.load_cert_bytes(pem)
    basic = issued.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic.value.path_length == 1
    assert crypto.verify_cert_signed_by(issued, root_cert)


def test_root_revoke_issue_touch_verify(tmp_path, monkeypatch):
    root_key, root_cert = _root()
    monkeypatch.setattr(crypto, "root_ca", lambda: root_cert)
    root = tmp_path / "repo"
    root.mkdir()
    doc = root_revoke.empty_doc(crypto.cert_info(root_cert))
    root_revoke.save(root, doc)
    root_revoke.issue(
        root,
        serial="abc123",
        san="hpm://official",
        subject="CN=HPM Official Registry",
        cert_fingerprint="AA:BB",
        reason="keyCompromise",
    )
    root_revoke.sign(root, root_key)
    ok, detail = root_revoke.verify(root)
    assert ok, detail
    assert root_revoke.is_revoked(root, serial="ABC123")

    before = root_revoke.load(root)["entries"]
    refreshed = root_revoke.touch(root, crypto.cert_info(root_cert))
    assert refreshed["entries"] == before
    root_revoke.sign(root, root_key)
    ok, detail = root_revoke.verify(root)
    assert ok, detail
