# -*- coding: utf-8 -*-
"""密钥、证书、签名请求测试。"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from hpmrepo.core import crypto, developers as dev_mod, keys as keys_mod
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
    official = keys_mod.find_official("test")
    assert official is not None
    path, issued = dev_mod.issue_request(
        domain_repo, request, crypto.load_private_key(official, None)
    )
    assert path.exists()
    assert issued.san == ["hpm://developer/bob"]


def test_cannot_issue_domain_from_domain(domain_repo):
    key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(key, kind="domain", name="other")
    request = dev_mod.validate_request_data(doc, domain_repo)
    with pytest.raises(RequestInvalid):
        official = keys_mod.find_official("test")
        assert official is not None
        dev_mod.issue_request(
            domain_repo,
            request,
            crypto.load_private_key(official, None),
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


def test_request_inbox_listing_and_removal(domain_repo):
    from hpmrepo.core import keys as keys_mod
    from hpmrepo.repo import request_inbox

    inbox = request_inbox()
    key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(key, kind="developer", name="carol")
    json_path, _ = dev_mod.write_request(inbox, doc, stem="developer-carol")
    summaries = dev_mod.list_request_summaries(domain_repo)
    target = [item for item in summaries if item.path == json_path]
    assert target and target[0].valid
    assert target[0].kind == "developer"
    assert target[0].name == "carol"

    official = keys_mod.find_official("test")
    assert official is not None
    request = dev_mod.load_request(json_path, domain_repo)
    dev_mod.issue_request(
        domain_repo, request, crypto.load_private_key(official, None)
    )
    assert not json_path.exists()


def test_unencrypted_key_is_encrypted_on_load(tmp_path, monkeypatch):
    from hpmrepo.views import common

    monkeypatch.delenv("HPM_ALLOW_UNENCRYPTED", raising=False)
    key_path = tmp_path / "official.key"
    private_pem, _ = crypto.generate_ed25519_keypair(None)
    key_path.write_bytes(private_pem)

    class FakeApp:
        def __init__(self):
            self.cache = {}
            self.errors = []

        def confirm(self, *args, **kwargs):
            return True

        def input(self, title, prompt, **kwargs):
            return "password123"

        def error(self, message, hint=""):
            self.errors.append(message)

        def cached_key(self, path):
            return self.cache.get(path)

        def cache_key(self, path, key):
            self.cache[path] = key

    app = FakeApp()
    key = common.load_key_with_password(app, key_path, "official")
    assert key is not None
    assert not app.errors
    assert b"ENCRYPTED" in key_path.read_bytes()
    with pytest.raises(PasswordError):
        crypto.load_private_key(key_path, None)
    assert crypto.load_private_key(key_path, "password123")


def test_encrypted_detection_for_legacy_pem(tmp_path):
    path = tmp_path / "legacy.key"
    path.write_text(
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "Proc-Type: 4,ENCRYPTED\n"
        "DEK-Info: AES-256-CBC,00000000000000000000000000000000\n\n"
        "AAAA\n"
        "-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    assert crypto.private_key_is_encrypted(path)


def test_find_official_prefers_cert_matching_key(tmp_path, monkeypatch):
    from hpmrepo.core import keys as keys_mod

    monkeypatch.setenv("HPM_HOME", str(tmp_path / "hpm"))
    wrong = ed25519.Ed25519PrivateKey.generate()
    right = ed25519.Ed25519PrivateKey.generate()
    wrong_path = keys_mod.official_key_path("")
    right_path = keys_mod.official_key_path("test")
    for path, key in ((wrong_path, wrong), (right_path, right)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            key.private_bytes(
                __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM,
                __import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.PKCS8,
                __import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption(),
            )
        )
        path.with_suffix(".pub").write_bytes(crypto.public_key_pem(key))

    ca_key = ed25519.Ed25519PrivateKey.generate()
    from cryptography import x509
    import datetime as dt
    from cryptography.x509.oid import NameOID

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HPM Test")])
    now = dt.datetime.now(dt.timezone.utc)
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(2)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(ca_key, None)
    )
    cert_path = tmp_path / "official.crt"
    cert_path.write_bytes(
        crypto.issue_certificate(
            ca,
            ca_key,
            right.public_key(),
            "HPM Domain test",
            "hpm://domain/test",
            profile="domain",
        )
    )
    assert keys_mod.find_official("test", cert_path) == right_path


def test_find_official_skips_wrong_plaintext_key(tmp_path, monkeypatch):
    from hpmrepo.core import keys as keys_mod

    monkeypatch.setenv("HPM_HOME", str(tmp_path / "hpm"))
    wrong = ed25519.Ed25519PrivateKey.generate()
    wrong_path = keys_mod.official_key_path("")
    wrong_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_path.write_bytes(
        wrong.private_bytes(
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM,
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.PKCS8,
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption(),
        )
    )
    right_pem, _ = crypto.generate_ed25519_keypair("password123")
    right_path = keys_mod.official_key_path("test")
    right_path.parent.mkdir(parents=True, exist_ok=True)
    right_path.write_bytes(right_pem)
    right_key = crypto.load_private_key(right_path, "password123")

    ca_key = ed25519.Ed25519PrivateKey.generate()
    from cryptography import x509
    import datetime as dt
    from cryptography.x509.oid import NameOID

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HPM Test")])
    now = dt.datetime.now(dt.timezone.utc)
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(3)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(ca_key, None)
    )
    cert_path = tmp_path / "official.crt"
    cert_path.write_bytes(
        crypto.issue_certificate(
            ca,
            ca_key,
            right_key.public_key(),
            "HPM Domain test",
            "hpm://domain/test",
            profile="domain",
        )
    )
    assert keys_mod.find_official("test", cert_path) == right_path


def test_openssh_private_key_support(tmp_path):
    from cryptography.hazmat.primitives import serialization

    key = ed25519.Ed25519PrivateKey.generate()
    encrypted = tmp_path / "id_ed25519"
    encrypted.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.BestAvailableEncryption(b"testpass"),
        )
    )
    plain = tmp_path / "id_ed25519_plain"
    plain.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    assert crypto.private_key_is_encrypted(encrypted)
    assert not crypto.private_key_is_encrypted(plain)
    with pytest.raises(PasswordError):
        crypto.load_private_key(encrypted, None)
    with pytest.raises(PasswordError):
        crypto.load_private_key(encrypted, "wrong")
    loaded = crypto.load_private_key(encrypted, "testpass")
    assert len(crypto.sign_bytes(loaded, b"data")) == 64
    assert crypto.load_private_key(plain, None)
