# -*- coding: utf-8 -*-
"""密钥、证书、签名请求测试。"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from hpmrepo.core import crypto, developers as dev_mod
from hpmrepo.util.errors import PasswordError, RequestInvalid


def test_keypair_password_roundtrip(tmp_path):
    private_pem, public_pem = crypto.generate_ed25519_keypair("hunter2!")
    key_path = tmp_path / "k.key"
    key_path.write_bytes(private_pem)
    assert crypto.private_key_is_encrypted(key_path)
    with pytest.raises(PasswordError):
        crypto.load_private_key(key_path, None)
    with pytest.raises(PasswordError):
        crypto.load_private_key(key_path, "wrong")
    key = crypto.load_private_key(key_path, "hunter2!")
    data = b"payload"
    sig = crypto.sign_bytes(key, data)
    pub = crypto.load_public_key(public_pem)
    assert crypto.verify_bytes(pub, data, sig)
    assert not crypto.verify_bytes(pub, data + b"x", sig)


def test_request_roundtrip_and_tamper(tmp_path):
    key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(key, kind="developer", name="alice")
    request = dev_mod.validate_request_data(doc, tmp_path)
    assert request.san == "hpm://developer/alice"

    tampered = dict(doc)
    tampered["name"] = "mallory"
    with pytest.raises(RequestInvalid):
        dev_mod.validate_request_data(tampered, tmp_path)

    bad_sig = dict(doc)
    bad_sig["selfSignature"] = "AAAA"
    with pytest.raises(RequestInvalid):
        dev_mod.validate_request_data(bad_sig, tmp_path)

    reserved = dev_mod.build_request(key, kind="developer", name="repo-tool")
    with pytest.raises(RequestInvalid):
        dev_mod.validate_request_data(reserved, tmp_path)


def test_issue_developer_certificate(domain_repo, developer_factory):
    dev_key, cert_path, info = developer_factory("alice")
    assert cert_path == domain_repo / "alice" / "alice.crt"
    assert info.san == ["hpm://developer/alice"]
    assert not info.is_ca
    assert crypto.verify_cert_signed_by(
        crypto.load_cert(cert_path), crypto.load_cert(domain_repo / "official.crt")
    )
    assert (domain_repo / "requests").is_dir()

    # 包签名
    tar = domain_repo / "alice" / "demo-ch552-1.0.0.tar.gz"
    tar.write_bytes(b"tarball")
    sig = crypto.sign_bytes(dev_key, tar.read_bytes())
    assert crypto.verify_signature_with_cert(
        crypto.load_cert(cert_path), tar.read_bytes(), sig
    )


def test_domain_certificate_is_ca(domain_repo, ca, tmp_path):
    ca_key, ca_cert, _ = ca
    domain_cert = crypto.load_cert(domain_repo / "official.crt")
    info = crypto.cert_info(domain_cert)
    assert info.is_ca
    assert info.san == ["hpm://domain/test"]
    # 域证书可以签发开发者证书
    dev_key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(dev_key, kind="developer", name="bob")
    request = dev_mod.validate_request_data(doc, domain_repo)
    path, issued = dev_mod.issue_request(domain_repo, request, crypto.load_private_key(
        domain_repo / "official.key", None
    ))
    assert path.exists()
    assert issued.san == ["hpm://developer/bob"]


def test_cannot_issue_domain_from_domain(domain_repo):
    key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(key, kind="domain", name="other")
    request = dev_mod.validate_request_data(doc, domain_repo)
    with pytest.raises(RequestInvalid):
        dev_mod.issue_request(
            domain_repo,
            request,
            crypto.load_private_key(domain_repo / "official.key", None),
        )


def test_official_can_issue_domain(repo):
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import NameOID

    ca_key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "HPM Official Test"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HPM"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier("hpm://official")]
            ),
            critical=False,
        )
        .sign(ca_key, None)
    )
    (repo / "official.crt").write_bytes(
        ca_cert.public_bytes(serialization.Encoding.PEM)
    )
    (repo / "official.key").write_bytes(
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(key, kind="domain", name="newdom")
    request = dev_mod.validate_request_data(doc, repo)
    cert_path, info = dev_mod.issue_request(repo, request, ca_key)
    assert cert_path == repo / "outgoing" / "newdom.crt"
    assert info.san == ["hpm://domain/newdom"]
    assert info.is_ca
    issued = crypto.load_cert(cert_path)
    basic = issued.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic.value.path_length == 0
    assert crypto.verify_cert_signed_by(issued, ca_cert)
