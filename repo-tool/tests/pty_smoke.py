#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TUI 冒烟测试（pty 驱动，不依赖 pytest）。

用法（在 repo-tool 目录）：
    python3 tests/pty_smoke.py
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))
HRM = TOOL_DIR / "hrm"
REPO_ROOT = TOOL_DIR.parent

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain(data: bytes) -> str:
    return ANSI_RE.sub("", data.decode("utf-8", "replace")).replace("\r", "")


class Session:
    def __init__(self, env: dict[str, str], *args: str, cols: int = 110, rows: int = 34):
        master, slave = pty.openpty()
        import fcntl

        fcntl.ioctl(
            slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)
        )
        environment = dict(os.environ, TERM="xterm-256color")
        environment.pop("NO_COLOR", None)
        environment.update(env)
        self.proc = subprocess.Popen(
            [str(HRM), *args],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            cwd=str(TOOL_DIR),
            close_fds=True,
        )
        os.close(slave)
        self.master = master
        self.buffer = b""

    def drain(self, seconds: float = 0.05) -> str:
        end = time.monotonic() + seconds
        out = b""
        while time.monotonic() < end:
            readable, _, _ = select.select([self.master], [], [], 0.01)
            if not readable:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        self.buffer += out
        return plain(out)

    def wait_for(self, needle: str, timeout: float = 6.0) -> str:
        # 只匹配本次等待期间新绘制的内容，避免命中历史画面
        self.buffer = b""
        end = time.monotonic() + timeout
        acc = ""
        while time.monotonic() < end:
            acc += self.drain(0.05)
            if needle in acc:
                return acc
        raise AssertionError(f"等待界面文本超时: {needle!r}")

    def send(self, text: str) -> None:
        os.write(self.master, text.encode())

    def close(self) -> int:
        self.send("q")
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=3)
        self.drain(0.2)
        os.close(self.master)
        return self.proc.returncode


def requests_dir(home: Path) -> Path:
    return home.parent / "requests"


def domain_key_path(home: Path, domain: str = "test") -> Path:
    return home / "keys" / "domain" / domain / "official.key"


def make_env(root: Path, home: Path, **extra: str) -> dict[str, str]:
    env = {
        "HPM_REPO_ROOT": str(root),
        "HPM_HOME": str(home),
        "HPM_REQUESTS_DIR": str(requests_dir(home)),
        "HPM_ALLOW_UNENCRYPTED": "1",
        "HPM_DISABLE_CENTER_SYNC": "1",
        "HPM_OFFICIAL_CERT": extra.pop(
            "HPM_OFFICIAL_CERT", str(Path.home() / ".hpm" / "official.crt")
        ),
    }
    env.update(extra)
    return env


def scenario_home() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        session = Session(make_env(root, home), "--no-animation")
        session.wait_for("生成签名请求")
        text = plain(session.buffer)
        assert "管理包" not in text, "未注册用户不应出现管理包"
        session.send("\r")
        session.wait_for("私域仓库请求")
        session.send("\x1b")
        session.wait_for("生成签名请求")
        code = session.close()
        assert code == 0, code


def scenario_official() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        (root / "official.crt").write_bytes((REPO_ROOT / "official.crt").read_bytes())
        home = base / "home"
        home.mkdir()
        key_path = home / "keys" / "official.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("-----BEGIN PRIVATE KEY-----\ndummy\n-----END PRIVATE KEY-----\n", encoding="utf-8")
        session = Session(make_env(root, home), "--no-animation")
        session.wait_for("管理包")
        text = plain(session.buffer)
        assert "登录开发者中心" not in text
        session.send("\r")  # 管理包
        session.wait_for("发布包（仅签名入库）")
        session.send("\x1b")
        session.send("q")
        code = session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)
        assert code == 0, code


def scenario_init() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        (home / "dev.json").write_text(
            json.dumps(
                {
                    "accessToken": "token",
                    "expiresAt": "2999-01-01T00:00:00Z",
                    "developer": "tester",
                }
            ),
            encoding="utf-8",
        )
        session = Session(make_env(root, home), "--no-animation")
        session.wait_for("初始化私域仓库")
        session.send("\r")
        session.wait_for("将执行")
        session.send("\r")
        session.wait_for("域名")
        session.send("\r")
        session.wait_for("联系邮箱")
        session.send("\r")
        session.wait_for("Git 远程地址")
        session.send("\r")
        session.wait_for("设置密码")
        session.send("password123\r")
        session.wait_for("再次输入密码")
        session.send("password123\r")
        session.wait_for("确认开始初始化")
        session.send("\x1b[C")
        time.sleep(0.3)
        session.send("\r")
        session.wait_for("签名请求")
        key_path = domain_key_path(home, "tester")
        assert key_path.is_file()
        assert b"ENCRYPTED" in key_path.read_bytes()
        assert (root / "official.request.json").is_file()
        assert (requests_dir(home) / "domain-tester.request.json").is_file()
        assert (root / ".git").is_dir()
        session.send("\r")
        time.sleep(0.4)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)
        request = json.loads((root / "official.request.json").read_text(encoding="utf-8"))
        assert request["kind"] == "domain"
        assert request["name"] == "tester"
        assert request["san"] == "hpm://domain/tester"


def scenario_splash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        session = Session(make_env(root, home))
        session.wait_for("按任意键跳过")
        session.send(" ")
        session.wait_for("生成签名请求")
        code = session.close()
        assert code == 0, code


def _prepare_signed_repo(root: Path, home: Path, issuer_path: Path) -> None:
    from hpmrepo.core import index as index_mod, revoke
    from hpmrepo.core import crypto

    _make_domain_repo(root, home, issuer_path)
    index_mod.write_empty_index(root)
    revoke.save(root, revoke.empty_doc())
    key = crypto.load_private_key(domain_key_path(home), None)
    index_mod.sign_index(root, key)
    revoke.sign(root, key)


def scenario_finish_init() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _make_domain_repo(root, home, issuer)
        from hpmrepo.core import index as index_mod, revoke

        index_mod.write_empty_index(root)
        revoke.save(root, revoke.empty_doc())
        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("完成初始化")
        session.send("\r")
        session.wait_for("完成初始化")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("签名")
        time.sleep(1.2)
        session.send("\x1b")
        time.sleep(0.3)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)
        ok, detail = index_mod.verify_index(root)
        assert ok, detail
        ok, detail = revoke.verify(root)
        assert ok, detail


def scenario_revoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _prepare_signed_repo(root, home, issuer)
        _make_request(home, "alice")
        from hpmrepo.core import crypto, developers as dev_mod, index as index_mod

        req = dev_mod.load_request(
            requests_dir(home) / "developer-alice.request.json", root
        )
        key = crypto.load_private_key(domain_key_path(home), None)
        dev_mod.issue_request(root, req, key)
        index_mod.rebuild(root)
        index_mod.sign_index(root, key)

        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("管理开发者")
        session.send("\x1b[B")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("吊销证书")
        session.send("\x1b[B")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("选择要吊销的证书")
        session.send("\r")
        session.wait_for("吊销证书")
        session.send("\x1b[C")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("吊销原因")
        session.send("\r")
        session.wait_for("已吊销 1")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (root / "revoke.json.sig").is_file():
            time.sleep(0.05)
        assert (root / "revoke.json.sig").is_file(), "revoke.json.sig 未生成"
        session.send("\x1b")
        time.sleep(0.2)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)
        from hpmrepo.core import revoke

        assert revoke.load(root)["entries"], "revoke.json 中没有 alice"
        ok, detail = revoke.verify(root)
        assert ok, detail


def scenario_publish() -> None:
    import io
    import tarfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _prepare_signed_repo(root, home, issuer)

        # 开发者 alice 与私钥
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from hpmrepo.core import crypto, developers as dev_mod, index as index_mod

        dev_key = ed25519.Ed25519PrivateKey.generate()
        doc = dev_mod.build_request(dev_key, kind="developer", name="alice")
        inbox = requests_dir(home)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "developer-alice.request.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )
        req = dev_mod.load_request(inbox / "developer-alice.request.json", root)
        official_key = crypto.load_private_key(domain_key_path(home), None)
        dev_mod.issue_request(root, req, official_key)
        dev_key_path = home / "keys" / "dev" / "alice" / "alice.key"
        dev_key_path.parent.mkdir(parents=True, exist_ok=True)
        dev_key_path.write_bytes(
            dev_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

        # 待发布包
        publish = root / "publish"
        publish.mkdir()
        tar_path = publish / "demo-ch552-1.0.0.tar.gz"
        manifest = {
            "name": "demo",
            "version": "1.0.0",
            "chip": "ch552",
            "description": "demo driver",
            "provides": {"methods": []},
            "requires": {},
        }
        with tarfile.open(tar_path, "w:gz") as tar:
            data = json.dumps(manifest).encode()
            info = tarfile.TarInfo("alice/manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        # 让默认开发者名为 alice
        (home / "dev.json").write_text(
            json.dumps(
                {
                    "accessToken": "token",
                    "expiresAt": "2999-01-01T00:00:00Z",
                    "developer": "alice",
                    "repoUrl": "https://example.com/repo",
                }
            ),
            encoding="utf-8",
        )
        index_mod.rebuild(root)
        index_mod.sign_index(root, official_key)

        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("管理包")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("发布包并更新索引")
        session.send("\x1b[B")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("请输入 tar.gz 路径")
        session.send("\r")
        session.wait_for("开发者名")
        session.send("\r")

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not (root / "alice" / tar_path.name).is_file():
            time.sleep(0.1)
        assert (root / "alice" / tar_path.name).is_file(), "包未入库"
        assert (root / "alice" / (tar_path.name + ".sig")).is_file(), "包未签名"
        ok, detail = index_mod.verify_index(root)
        assert ok, detail
        assert "demo-ch552" in index_mod.load_index(root)["packages"]

        session.send("\x1b")
        time.sleep(0.2)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def scenario_request_without_login() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        session = Session(make_env(root, home), "--no-animation")
        session.wait_for("生成签名请求")
        session.send("\r")
        session.wait_for("私域仓库请求")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("开发者名称")
        session.send("bob\r")
        session.wait_for("联系邮箱")
        session.send("\r")
        session.wait_for("GitHub 地址")
        session.send("\r")
        session.wait_for("生成新的 Ed25519 私钥")
        session.send("\r")
        session.wait_for("为新的 Ed25519 私钥设置密码")
        session.send("password123\r")
        session.wait_for("请再次输入密码")
        session.send("password123\r")
        session.wait_for("签名请求已生成")
        request_path = requests_dir(home) / "developer-bob.request.json"
        assert request_path.is_file(), "请求文件未生成"
        key_path = home / "keys" / "dev" / "bob" / "bob.key"
        assert key_path.is_file(), "私钥未生成"
        assert b"ENCRYPTED" in key_path.read_bytes()
        from hpmrepo.core import developers as dev_mod

        request = dev_mod.load_request(request_path, root)
        assert request.kind == "developer"
        assert request.san == "hpm://developer/bob"
        session.send("\r")
        time.sleep(0.3)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def _make_official_repo(base: Path, root: Path, home: Path):
    """构造一个由测试根 CA 签发的官方仓库。"""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.x509.oid import NameOID
    from hpmrepo.core import crypto

    root_key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "HPM Test Root"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HPM"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(root_key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(root_key, None)
    )
    root_pem = root_cert.public_bytes(serialization.Encoding.PEM)
    (base / "root.crt").write_bytes(root_pem)

    official_key = ed25519.Ed25519PrivateKey.generate()
    csr = crypto.load_csr_bytes(
        crypto.create_csr(official_key, "HPM Official Test", "hpm://official")
    )
    official_pem = crypto.issue_certificate(
        root_cert,
        root_key,
        csr.public_key(),
        "HPM Official Test",
        "hpm://official",
        profile="domain",
        days=3650,
    )
    (root / "official.crt").write_bytes(official_pem)
    key_path = home / "keys" / "official.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        official_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return root_pem, crypto.load_cert_bytes(official_pem)


def scenario_sign_domain_from_home() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        root_pem, official_cert = _make_official_repo(base, root, home)

        from cryptography.hazmat.primitives.asymmetric import ed25519
        from hpmrepo.core import crypto, developers as dev_mod

        key = ed25519.Ed25519PrivateKey.generate()
        doc = dev_mod.build_request(
            key,
            kind="domain",
            name="newdom",
            repo_url="https://github.com/example/hpm-repo",
        )
        inbox = requests_dir(home)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "domain-newdom.request.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )

        env = make_env(root, home, HPM_ROOT_CA=str(base / "root.crt"))
        session = Session(env, "--no-animation")
        session.wait_for("私域管理")
        session.send("\x1b[B")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("签发证书")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("选择签名请求")
        session.send("\r")
        session.wait_for("签发域证书")
        session.send("\x1b[C")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("域证书已生成")
        cert_path = root / "outgoing" / "newdom.crt"
        assert cert_path.is_file(), "域证书未生成"
        issued = crypto.load_cert(cert_path)
        assert crypto.verify_cert_signed_by(issued, official_cert)
        from hpmrepo.core import domains as domains_mod, index as index_mod

        assert "newdom" in domains_mod.load_domain_config(root)
        index_data = index_mod.load_index(root)
        assert "newdom" in index_data["domains"]
        ok, detail = index_mod.verify_index(root)
        assert ok, detail
        session.send("\r")
        time.sleep(0.3)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def scenario_no_requests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _prepare_signed_repo(root, home, issuer)

        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("管理开发者")
        for _ in range(2):
            session.send("\x1b[B")
            time.sleep(0.15)
        session.send("\r")
        session.wait_for("添加开发者")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("没有待签发的开发者请求")
        text = session.drain(0.5)
        assert "请输入 .request.json 路径" not in text
        session.send("\r")
        time.sleep(0.2)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def scenario_domain_management() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        _make_official_repo(base, root, home)

        from cryptography.hazmat.primitives.asymmetric import ed25519
        from hpmrepo.core import crypto, developers as dev_mod, revoke

        official_key = crypto.load_private_key(home / "keys" / "official.key", None)
        key = ed25519.Ed25519PrivateKey.generate()
        doc = dev_mod.build_request(
            key,
            kind="domain",
            name="newdom",
            repo_url="https://github.com/example/hpm-repo",
        )
        inbox = requests_dir(home)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "domain-newdom.request.json").write_text(json.dumps(doc), encoding="utf-8")
        req = dev_mod.load_request(inbox / "domain-newdom.request.json", root)
        dev_mod.issue_request(root, req, official_key)
        (root / "domain.yaml").write_text(
            "domains:\n"
            "  newdom:\n"
            "    index: https://example.com/index.json\n"
            "    cert: https://example.com/official.crt\n",
            encoding="utf-8",
        )

        env = make_env(root, home, HPM_ROOT_CA=str(base / "root.crt"))
        session = Session(env, "--no-animation")
        session.wait_for("私域管理")
        session.send("\x1b[B")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("私域列表")
        session.send("\r")
        text = session.wait_for("newdom")
        assert "已登记" in text
        # 进入详情，Esc 应返回列表
        session.send("\r")
        session.wait_for("域 newdom")
        session.send("\x1b")
        session.wait_for("私域列表")
        # 再从列表 Esc 回到私域管理
        session.send("\x1b")
        time.sleep(0.3)
        session.send("\x1b[B")
        session.send("\x1b[B")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.wait_for("吊销证书")
        session.send("\r")
        session.wait_for("选择要吊销的域证书")
        session.send("\r")
        session.wait_for("吊销域证书")
        session.send("\x1b[C")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("吊销原因")
        session.send("\r")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not revoke.load(root)["entries"]:
            time.sleep(0.05)
        assert revoke.load(root)["entries"], "no revoke entry"
        ok, detail = revoke.verify(root)
        assert ok, detail
        session.send("\x1b")
        time.sleep(0.2)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def scenario_delete_developer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _prepare_signed_repo(root, home, issuer)
        _make_request(home, "alice")
        from hpmrepo.core import crypto, developers as dev_mod, index as index_mod, revoke

        key = crypto.load_private_key(domain_key_path(home), None)
        req = dev_mod.load_request(
            requests_dir(home) / "developer-alice.request.json", root
        )
        dev_mod.issue_request(root, req, key)
        index_mod.rebuild(root)
        index_mod.sign_index(root, key)

        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("管理开发者")
        for _ in range(2):
            session.send("\x1b[B")
            time.sleep(0.15)
        session.send("\r")
        session.wait_for("删除开发者")
        for _ in range(3):
            session.send("\x1b[B")
            time.sleep(0.15)
        session.send("\r")
        session.wait_for("选择要删除的开发者")
        session.send("\r")
        session.wait_for("删除开发者")
        session.send("\x1b[C")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("删除方式")
        session.send("\r")  # 先吊销再删除
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and (root / "alice").exists():
            time.sleep(0.05)
        assert not (root / "alice").exists(), "开发者目录未删除"
        data = index_mod.load_index(root)
        assert "alice" not in data["developers"]
        assert revoke.load(root)["entries"]
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def scenario_delete_domain() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        _make_official_repo(base, root, home)

        from cryptography.hazmat.primitives.asymmetric import ed25519
        from hpmrepo.core import crypto, developers as dev_mod, domains as domains_mod, index as index_mod

        official_key = crypto.load_private_key(home / "keys" / "official.key", None)
        key = ed25519.Ed25519PrivateKey.generate()
        doc = dev_mod.build_request(
            key,
            kind="domain",
            name="newdom",
            repo_url="https://github.com/example/hpm-repo",
        )
        inbox = requests_dir(home)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "domain-newdom.request.json").write_text(json.dumps(doc), encoding="utf-8")
        req = dev_mod.load_request(inbox / "domain-newdom.request.json", root)
        dev_mod.issue_request(root, req, official_key)
        index_url, cert_url = domains_mod.derive_urls(req.repo_url, req.github_url)
        domains_mod.register_domain(root, "newdom", index_url, cert_url)
        index_mod.rebuild(root)
        index_mod.sign_index(root, official_key)

        env = make_env(root, home, HPM_ROOT_CA=str(base / "root.crt"))
        session = Session(env, "--no-animation")
        session.wait_for("私域管理")
        session.send("\x1b[B")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("私域列表")
        for _ in range(4):
            session.send("\x1b[B")
            time.sleep(0.12)
        session.send("\r")
        session.wait_for("选择要删除的域")
        session.send("\r")
        session.wait_for("删除域")
        session.send("\x1b[C")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("删除方式")
        session.send("\r")
        deadline = time.monotonic() + 6
        while (
            time.monotonic() < deadline
            and "newdom" in domains_mod.load_domain_config(root)
        ):
            time.sleep(0.05)
        assert "newdom" not in domains_mod.load_domain_config(root)
        assert not (root / "outgoing" / "newdom.crt").exists()
        data = index_mod.load_index(root)
        assert "newdom" not in data["domains"]
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def scenario_refresh_revoke_date() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _prepare_signed_repo(root, home, issuer)
        from hpmrepo.core import crypto, index as index_mod, revoke

        doc = revoke.load(root)
        doc["updatedAt"] = "2000-01-01T00:00:00Z"
        revoke.save(root, doc)
        index_mod.sign_index(root, crypto.load_private_key(domain_key_path(home), None))

        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("管理包")
        for _ in range(4):
            session.send("\x1b[B")
            time.sleep(0.12)
        session.wait_for("更新吊销列表")
        session.send("\r")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if revoke.load(root)["updatedAt"] != "2000-01-01T00:00:00Z":
                break
            time.sleep(0.05)
        assert revoke.load(root)["updatedAt"] != "2000-01-01T00:00:00Z"
        ok, detail = revoke.verify(root)
        assert ok, detail
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def scenario_publish_only_then_index() -> None:
    import io
    import tarfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _prepare_signed_repo(root, home, issuer)

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from hpmrepo.core import crypto, developers as dev_mod, index as index_mod

        dev_key = ed25519.Ed25519PrivateKey.generate()
        doc = dev_mod.build_request(dev_key, kind="developer", name="alice")
        inbox = requests_dir(home)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "developer-alice.request.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )
        req = dev_mod.load_request(inbox / "developer-alice.request.json", root)
        official_key = crypto.load_private_key(domain_key_path(home), None)
        dev_mod.issue_request(root, req, official_key)
        (root / "alice" / "alice.key").unlink(missing_ok=True)
        dev_key_path = home / "keys" / "dev" / "alice" / "alice.key"
        dev_key_path.parent.mkdir(parents=True, exist_ok=True)
        dev_key_path.write_bytes(
            dev_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

        publish = root / "publish"
        publish.mkdir()
        tar_path = publish / "demo-ch552-1.0.0.tar.gz"
        manifest = {
            "name": "demo",
            "version": "1.0.0",
            "chip": "ch552",
            "description": "demo driver",
            "provides": {"methods": []},
            "requires": {},
        }
        with tarfile.open(tar_path, "w:gz") as archive:
            data = json.dumps(manifest).encode()
            info = tarfile.TarInfo("alice/manifest.json")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

        (home / "dev.json").write_text(
            json.dumps(
                {
                    "accessToken": "token",
                    "expiresAt": "2999-01-01T00:00:00Z",
                    "developer": "alice",
                }
            ),
            encoding="utf-8",
        )
        index_mod.rebuild(root)
        index_mod.sign_index(root, official_key)

        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("管理包")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("发布包（仅签名入库）")
        session.send("\x1b[B")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("请输入 tar.gz 路径")
        session.send("\r")
        session.wait_for("开发者名")
        session.send("\r")
        session.wait_for("包已入库")
        assert "demo-ch552" not in index_mod.load_index(root)["packages"]
        session.send("\r")
        time.sleep(0.3)
        # 回到包列表：当前光标在「发布包（仅签名入库）」，上移一项
        session.send("\x1b[A")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("未加入索引")
        session.send("\r")
        session.wait_for("索引:     未加入")
        session.send("a")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            data = index_mod.load_index(root)
            if "demo-ch552" in (data.get("packages") or {}):
                break
            time.sleep(0.05)
        assert "demo-ch552" in index_mod.load_index(root)["packages"]
        ok, detail = index_mod.verify_index(root)
        assert ok, detail
        session.send("\x1b")
        time.sleep(0.2)
        session.send("\x1b")
        time.sleep(0.2)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)


def main() -> int:
    scenarios = [
        ("home/gating", scenario_home),
        ("official menu", scenario_official),
        ("init private repo", scenario_init),
        ("add developer", scenario_developer),
        ("finish init", scenario_finish_init),
        ("revoke certificate", scenario_revoke),
        ("publish package", scenario_publish),
        ("request without login", scenario_request_without_login),
        ("sign domain from home", scenario_sign_domain_from_home),
        ("no request list", scenario_no_requests),
        ("domain management", scenario_domain_management),
        ("delete developer", scenario_delete_developer),
        ("delete domain", scenario_delete_domain),
        ("refresh revoke date", scenario_refresh_revoke_date),
        ("publish only then index", scenario_publish_only_then_index),
        ("splash", scenario_splash),
    ]
    failed = 0
    for name, func in scenarios:
        started = time.monotonic()
        try:
            func()
            print(f"PASS {name} ({time.monotonic() - started:.1f}s)")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {exc}")
    return 1 if failed else 0




def _make_domain_repo(root: Path, home: Path, issuer_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from hpmrepo.core import crypto

    import datetime as dt
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    ca_key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "HPM Test CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HPM"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    ca_cert_obj = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(0xCA)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(ca_key, None)
    )
    issuer_path.write_bytes(
        ca_cert_obj.public_bytes(serialization.Encoding.PEM)
    )
    domain_key = ed25519.Ed25519PrivateKey.generate()
    csr = crypto.load_csr_bytes(
        crypto.create_csr(domain_key, "HPM Domain test", "hpm://domain/test")
    )
    (root / "official.crt").write_bytes(
        crypto.issue_certificate(
            ca_cert_obj,
            ca_key,
            csr.public_key(),
            "HPM Domain test",
            "hpm://domain/test",
            profile="domain",
        )
    )
    key_path = domain_key_path(home)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        domain_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (home / "dev.json").write_text(
        json.dumps(
            {
                "accessToken": "token",
                "expiresAt": "2999-01-01T00:00:00Z",
                "developer": "tester",
                "repoUrl": "https://example.com/repo",
            }
        ),
        encoding="utf-8",
    )


def _make_request(home: Path, name: str) -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from hpmrepo.core import developers as dev_mod

    key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(key, kind="developer", name=name)
    inbox = requests_dir(home)
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"developer-{name}.request.json").write_text(
        json.dumps(doc), encoding="utf-8"
    )


def scenario_developer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        issuer = base / "issuer.crt"
        _make_domain_repo(root, home, issuer)
        _make_request(home, "alice")
        env = make_env(root, home, HPM_OFFICIAL_CERT=str(issuer))
        session = Session(env, "--no-animation")
        session.wait_for("管理开发者")
        # 主页：完成初始化 / 登录 / 管理包 / 管理开发者
        for _ in range(3):
            session.send("\x1b[B")
            time.sleep(0.15)
        session.send("\r")
        session.wait_for("添加开发者")
        session.send("\x1b[B")
        time.sleep(0.15)
        session.send("\r")
        session.wait_for("选择签名请求")
        session.send("\r")  # 选择第一个请求
        session.wait_for("签发开发者证书")
        session.send("\x1b[C")
        time.sleep(0.2)
        session.send("\r")
        session.wait_for("证书已生成")
        assert (root / "alice" / "alice.crt").is_file()
        session.send("\r")
        time.sleep(0.3)
        session.send("\x1b")
        time.sleep(0.3)
        session.send("\x1b")
        time.sleep(0.3)
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)
        assert (root / "outgoing" / "alice.crt").is_file()


if __name__ == "__main__":
    sys.exit(main())
