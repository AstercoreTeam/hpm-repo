# -*- coding: utf-8 -*-
"""测试夹具：临时仓库、一次性 CA、签名请求。"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from hpmrepo.core import crypto, developers as dev_mod

def make_ca() -> tuple[ed25519.Ed25519PrivateKey, x509.Certificate, bytes]:
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "HPM Test CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HPM"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(0x1234)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, None)
    )
    return key, cert, cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture
def ca():
    return make_ca()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("HPM_REPO_ROOT", str(root))
    monkeypatch.setenv("HPM_HOME", str(tmp_path / "hpm-home"))
    return root


@pytest.fixture
def domain_repo(repo, ca, monkeypatch):
    ca_key, ca_cert, ca_pem = ca
    issuer = repo / "issuer.crt"
    issuer.write_bytes(ca_pem)
    monkeypatch.setenv("HPM_OFFICIAL_CERT", str(issuer))

    domain_key = ed25519.Ed25519PrivateKey.generate()
    csr_pem = crypto.create_csr(domain_key, "HPM Domain test", "hpm://domain/test")
    domain_pem = crypto.issue_certificate(
        ca_cert,
        ca_key,
        crypto.load_csr_bytes(csr_pem).public_key(),
        "HPM Domain test",
        "hpm://domain/test",
        profile="domain",
    )
    (repo / "official.crt").write_bytes(domain_pem)
    (repo / "official.key").write_bytes(
        domain_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return repo


def add_developer(root: Path, ca, name: str = "alice"):
    from hpmrepo.core import crypto as crypto_mod

    ca_key = crypto_mod.load_private_key(root / "official.key", None)
    dev_key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(dev_key, kind="developer", name=name)
    incoming = root / "incoming"
    incoming.mkdir(exist_ok=True)
    path = incoming / f"{name}.request.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    req = dev_mod.load_request(path, root)
    cert_path, info = dev_mod.issue_request(root, req, ca_key)
    return dev_key, cert_path, info


@pytest.fixture
def developer_factory(domain_repo, ca):
    def make(name: str = "alice"):
        return add_developer(domain_repo, ca, name)

    return make
