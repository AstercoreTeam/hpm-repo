# -*- coding: utf-8 -*-
"""密码学操作：密钥、CSR、证书、签名。

优先使用 cryptography；Ed25519 为默认算法，同时兼容读取 RSA/ECDSA 密钥。
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ..util.errors import CryptoError, PasswordError

# 与 core/include/hpm/config.h 中的 HPM_ROOT_CA_PEM 保持一致
ROOT_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBcDCCASKgAwIBAgIUbxJStO+IYuUjGZ2GqCKWHabO2a4wBQYDK2VwMCQxFDAS
BgNVBAMMC0hQTSBSb290IENBMQwwCgYDVQQKDANIUE0wHhcNMjYxMTA2MTExMDM3
WhcNNDYxMTAxMTExMDM3WjAkMRQwEgYDVQQDDAtIUE0gUm9vdCBDQTEMMAoGA1UE
CgwDSFBNMCowBQYDK2VwAyEAxtoW0XMlNVBZWUco9jk2zH7FyigIbHVpPypatiOF
EBSjZjBkMBIGA1UdEwEB/wQIMAYBAf8CAQIwDgYDVR0PAQH/BAQDAgEGMB0GA1Ud
DgQWBBS9zLfXrrDF6+fT36E+3FTx7VRHSjAfBgNVHSMEGDAWgBS9zLfXrrDF6+fT
36E+3FTx7VRHSjAFBgMrZXADQQDLg8qbPEDKmB6i/Wcr7f1e57K9sWEVjw0QQKL/
OL5NFoAb12sRoQmdCgGrPIxf9iVQZlxL7uBFOvL5LF46jREA
-----END CERTIFICATE-----
"""



@dataclass
class CertInfo:
    subject: str
    issuer: str
    serial: str
    san: list[str]
    not_before: dt.datetime
    not_after: dt.datetime
    fingerprint: str
    is_ca: bool
    algorithm: str
    days_left: int

    @property
    def expired(self) -> bool:
        return self.days_left < 0

    @property
    def summary(self) -> str:
        return f"{self.subject} · {self.san[0] if self.san else '无 SAN'}"


@dataclass
class CsrInfo:
    subject: str
    san: list[str]
    serial_hint: str


@dataclass
class IssuedCert:
    pem: bytes
    info: CertInfo


# --------------------------------------------------------------------------
# 根证书 / 签发者
# --------------------------------------------------------------------------


def root_ca() -> x509.Certificate:
    env = os.environ.get("HPM_ROOT_CA")
    if env and Path(env).is_file():
        return load_cert(Path(env))
    return load_cert_bytes(ROOT_CA_PEM.encode())


def official_issuer_cert(root: Path | None = None) -> x509.Certificate | None:
    """用于校验开发者/域证书的官方证书。"""
    env = os.environ.get("HPM_OFFICIAL_CERT")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    if root is not None:
        candidates.append(root / "official-issuer.crt")
    candidates.append(Path.home() / ".hpm" / "official.crt")
    for path in candidates:
        if path.is_file():
            try:
                return load_cert(path)
            except CryptoError:
                continue
    return None


# --------------------------------------------------------------------------
# 证书 / CSR 读取
# --------------------------------------------------------------------------


def load_cert_bytes(data: bytes) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(data)
    except Exception as exc:  # noqa: BLE001
        raise CryptoError("证书解析失败", str(exc)) from exc


def load_cert(path: Path) -> x509.Certificate:
    try:
        return load_cert_bytes(Path(path).read_bytes())
    except CryptoError:
        raise
    except OSError as exc:
        raise CryptoError(f"无法读取证书: {path}", str(exc)) from exc


def cert_info(cert: x509.Certificate) -> CertInfo:
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san = [
            value.value
            for value in san_ext.value
            if isinstance(value, x509.UniformResourceIdentifier)
        ]
    except x509.ExtensionNotFound:
        san = []

    now = dt.datetime.now(dt.timezone.utc)
    not_after = cert.not_valid_after_utc
    not_before = cert.not_valid_before_utc
    days_left = (not_after - now).days

    try:
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        is_ca = bool(basic.value.ca)
    except x509.ExtensionNotFound:
        is_ca = False

    try:
        pub = cert.public_key()
        if isinstance(pub, ed25519.Ed25519PublicKey):
            algorithm = "Ed25519"
        elif isinstance(pub, rsa.RSAPublicKey):
            algorithm = "RSA"
        elif isinstance(pub, ec.EllipticCurvePublicKey):
            algorithm = "ECDSA"
        else:
            algorithm = type(pub).__name__
    except Exception:  # noqa: BLE001
        algorithm = "unknown"

    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        serial=format(cert.serial_number, "X"),
        san=san,
        not_before=not_before,
        not_after=not_after,
        fingerprint=fingerprint(cert),
        is_ca=is_ca,
        algorithm=algorithm,
        days_left=days_left,
    )


def fingerprint(cert: x509.Certificate) -> str:
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{b:02X}" for b in digest)


def load_csr_bytes(data: bytes) -> x509.CertificateSigningRequest:
    try:
        return x509.load_pem_x509_csr(data)
    except Exception as exc:  # noqa: BLE001
        raise CryptoError("签名请求解析失败", str(exc)) from exc


def load_csr(path: Path) -> x509.CertificateSigningRequest:
    try:
        return load_csr_bytes(Path(path).read_bytes())
    except OSError as exc:
        raise CryptoError(f"无法读取签名请求: {path}", str(exc)) from exc


def csr_info(csr: x509.CertificateSigningRequest) -> CsrInfo:
    san: list[str] = []
    try:
        ext = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san = [
            v.value
            for v in ext.value
            if isinstance(v, x509.UniformResourceIdentifier)
        ]
    except x509.ExtensionNotFound:
        pass
    return CsrInfo(
        subject=csr.subject.rfc4514_string(),
        san=san,
        serial_hint="",
    )


def csr_public_key_pem(csr: x509.CertificateSigningRequest) -> bytes:
    return csr.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# --------------------------------------------------------------------------
# 私钥
# --------------------------------------------------------------------------


def _is_openssh_key(data: bytes) -> bool:
    return b"-----BEGIN OPENSSH PRIVATE KEY-----" in data


def private_key_is_encrypted(path: Path) -> bool:
    """判断私钥是否需要密码。

    支持：
      - PKCS#8 加密 PEM（BEGIN ENCRYPTED PRIVATE KEY）
      - 旧式 OpenSSL 加密 PEM（Proc-Type: 4,ENCRYPTED）
      - OpenSSH 私钥（BEGIN OPENSSH PRIVATE KEY，bcrypt 加密）
    优先实际尝试无密码加载，避免只看头部误判。
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False

    if _is_openssh_key(data):
        try:
            serialization.load_ssh_private_key(data, password=None)
            return False
        except TypeError:
            return True
        except (ValueError, UnsupportedAlgorithm):
            return False

    text = data.decode("utf-8", errors="ignore")
    if (
        "BEGIN ENCRYPTED PRIVATE KEY" in text
        or "Proc-Type: 4,ENCRYPTED" in text
        or "DEK-Info:" in text
    ):
        return True
    try:
        serialization.load_pem_private_key(data, password=None)
        return False
    except TypeError:
        return True
    except (ValueError, UnsupportedAlgorithm):
        return False


def load_private_key(path: Path, password: str | None = None):
    data = Path(path).read_bytes()
    pw = password.encode() if password else None

    if _is_openssh_key(data):
        try:
            return serialization.load_ssh_private_key(data, password=pw)
        except TypeError as exc:
            message = str(exc).lower()
            if "password" in message and pw is None:
                raise PasswordError("私钥已加密，需要密码") from exc
            if "not encrypted" in message:
                try:
                    return serialization.load_ssh_private_key(data, password=None)
                except Exception as inner:  # noqa: BLE001
                    raise CryptoError("无法读取 OpenSSH 私钥", str(inner)) from inner
            raise CryptoError("无法读取 OpenSSH 私钥", str(exc)) from exc
        except ValueError as exc:
            if pw is not None:
                raise PasswordError("私钥密码错误", "请重试") from exc
            raise CryptoError("OpenSSH 私钥解析失败", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise CryptoError(f"无法读取私钥: {path}", str(exc)) from exc

    try:
        return serialization.load_pem_private_key(data, password=pw)
    except TypeError as exc:
        # 有密码但没给，或给了但不需要
        if private_key_is_encrypted(Path(path)):
            raise PasswordError("私钥已加密，需要密码") from exc
        raise CryptoError("私钥需要密码或格式不受支持", str(exc)) from exc
    except ValueError as exc:
        raise PasswordError("私钥密码错误", "请重试") from exc
    except Exception as exc:  # noqa: BLE001
        raise CryptoError(f"无法读取私钥: {path}", str(exc)) from exc

def load_private_key_auto(path: Path, password: str | None = None):
    """有密码就用密码；没密码先按无密码尝试。"""
    if password is not None:
        return load_private_key(path, password)
    try:
        return load_private_key(path, None)
    except PasswordError:
        raise


def generate_ed25519_keypair(password: str | None = None) -> tuple[bytes, bytes]:
    key = ed25519.Ed25519PrivateKey.generate()
    encryption = (
        serialization.BestAvailableEncryption(password.encode())
        if password
        else serialization.NoEncryption()
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        encryption,
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def encrypt_private_key_pem(key, password: str) -> bytes:
    """把已有的私钥重新保存为带密码的 PKCS#8 PEM。"""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password.encode()),
    )


def public_key_pem(key) -> bytes:
    pub = key.public_key() if hasattr(key, "public_key") else key
    return pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def load_public_key(pem: bytes):
    try:
        return serialization.load_pem_public_key(pem)
    except Exception as exc:  # noqa: BLE001
        raise CryptoError("公钥解析失败", str(exc)) from exc


def public_keys_equal(a, b) -> bool:
    return public_key_pem(a) == public_key_pem(b)


# --------------------------------------------------------------------------
# 签名
# --------------------------------------------------------------------------


def _sign_algorithm(key):
    if isinstance(key, (ed25519.Ed25519PrivateKey,)):
        return None
    return hashes.SHA256()


def sign_bytes(key, data: bytes) -> bytes:
    alg = _sign_algorithm(key)
    try:
        if alg is None:
            return key.sign(data)
        return key.sign(data, padding.PKCS1v15(), alg)
    except TypeError:
        return key.sign(data, ec.ECDSA(hashes.SHA256()))


def verify_bytes(pub, data: bytes, signature: bytes) -> bool:
    try:
        if isinstance(pub, ed25519.Ed25519PublicKey):
            pub.verify(signature, data)
            return True
        try:
            pub.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:  # noqa: BLE001
            pub.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
    except Exception:  # noqa: BLE001
        return False


def verify_signature_with_cert(cert: x509.Certificate, data: bytes, sig: bytes) -> bool:
    return verify_bytes(cert.public_key(), data, sig)


# --------------------------------------------------------------------------
# CSR / 证书签发
# --------------------------------------------------------------------------


def create_csr(key, common_name: str, san: str, org: str = "HPM") -> bytes:
    builder = x509.CertificateSigningRequestBuilder()
    builder = builder.subject_name(
        x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
            ]
        )
    )
    builder = builder.add_extension(
        x509.SubjectAlternativeName([x509.UniformResourceIdentifier(san)]),
        critical=False,
    )
    csr = builder.sign(key, _sign_algorithm(key))
    return csr.public_bytes(serialization.Encoding.PEM)


def make_serial() -> int:
    return secrets.randbits(127) | 1


def issue_certificate(
    ca_cert: x509.Certificate,
    ca_key,
    subject_public_key,
    common_name: str,
    san: str,
    *,
    profile: str = "developer",
    days: int | None = None,
    serial: int | None = None,
    org: str = "HPM",
) -> bytes:
    if profile not in ("developer", "domain", "official"):
        raise CryptoError(f"未知证书类型: {profile}")

    if days is None:
        days = 1095 if profile == "developer" else 1825
    is_ca = profile in ("domain", "official")
    path_length = 1 if profile == "official" else (0 if profile == "domain" else None)

    builder = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(subject_public_key)
        .serial_number(serial or make_serial())
        .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5))
        .not_valid_after(
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
        )
        .add_extension(
            x509.BasicConstraints(ca=is_ca, path_length=path_length),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(san)]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(subject_public_key),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_cert.public_key()
            ),
            critical=False,
        )
    )
    cert = builder.sign(ca_key, _sign_algorithm(ca_key))
    return cert.public_bytes(serialization.Encoding.PEM)


def issue_certificate_from_csr(
    ca_cert: x509.Certificate,
    ca_key,
    csr: x509.CertificateSigningRequest,
    common_name: str,
    san: str,
    *,
    profile: str = "developer",
    days: int | None = None,
) -> bytes:
    if not csr.is_signature_valid:
        raise CryptoError("CSR 自签名无效")
    return issue_certificate(
        ca_cert,
        ca_key,
        csr.public_key(),
        common_name,
        san,
        profile=profile,
        days=days,
    )


def verify_cert_signed_by(
    cert: x509.Certificate, issuer: x509.Certificate
) -> bool:
    try:
        pub = issuer.public_key()
        if isinstance(pub, ed25519.Ed25519PublicKey):
            pub.verify(cert.signature, cert.tbs_certificate_bytes)
        else:
            try:
                pub.verify(
                    cert.signature,
                    cert.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    cert.signature_hash_algorithm,
                )
            except Exception:  # noqa: BLE001
                pub.verify(
                    cert.signature,
                    cert.tbs_certificate_bytes,
                    ec.ECDSA(cert.signature_hash_algorithm),
                )
        return cert.issuer == issuer.subject
    except Exception:  # noqa: BLE001
        return False
