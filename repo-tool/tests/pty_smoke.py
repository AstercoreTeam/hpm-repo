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


def make_env(root: Path, home: Path, **extra: str) -> dict[str, str]:
    env = {
        "HPM_REPO_ROOT": str(root),
        "HPM_HOME": str(home),
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
        session.wait_for("登录开发者中心")
        text = plain(session.buffer)
        assert "管理包" not in text, "无 Token 时不应出现管理包"
        session.send("\r")
        session.wait_for("将执行")
        session.send("\x1b")
        session.wait_for("登录开发者中心")
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
        session = Session(make_env(root, home), "--no-animation")
        session.wait_for("管理包")
        text = plain(session.buffer)
        assert "登录开发者中心" not in text
        session.send("\r")  # 管理包
        session.wait_for("发布 / 签名包")
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
        assert (root / "official.key").is_file()
        assert b"ENCRYPTED" in (root / "official.key").read_bytes()
        assert (root / "official.request.json").is_file()
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
        session.wait_for("登录开发者中心")
        code = session.close()
        assert code == 0, code


def _prepare_signed_repo(root: Path, home: Path, issuer_path: Path) -> None:
    from hpmrepo.core import index as index_mod, revoke
    from hpmrepo.core import crypto

    _make_domain_repo(root, home, issuer_path)
    index_mod.write_empty_index(root)
    revoke.save(root, revoke.empty_doc())
    key = crypto.load_private_key(root / "official.key", None)
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
        _make_request(root, "alice")
        from hpmrepo.core import crypto, developers as dev_mod, index as index_mod

        req = dev_mod.load_request(root / "incoming" / "alice.request.json", root)
        key = crypto.load_private_key(root / "official.key", None)
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
        incoming = root / "incoming"
        incoming.mkdir(exist_ok=True)
        (incoming / "alice.request.json").write_text(json.dumps(doc), encoding="utf-8")
        req = dev_mod.load_request(incoming / "alice.request.json", root)
        official_key = crypto.load_private_key(root / "official.key", None)
        dev_mod.issue_request(root, req, official_key)
        (root / "alice" / "alice.key").write_bytes(
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
        session.wait_for("发布 / 签名包")
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


def main() -> int:
    scenarios = [
        ("home/gating", scenario_home),
        ("official menu", scenario_official),
        ("init private repo", scenario_init),
        ("add developer", scenario_developer),
        ("finish init", scenario_finish_init),
        ("revoke certificate", scenario_revoke),
        ("publish package", scenario_publish),
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
    (root / "official.key").write_bytes(
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


def _make_request(root: Path, name: str) -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from hpmrepo.core import developers as dev_mod

    key = ed25519.Ed25519PrivateKey.generate()
    doc = dev_mod.build_request(key, kind="developer", name=name)
    incoming = root / "incoming"
    incoming.mkdir(exist_ok=True)
    (incoming / f"{name}.request.json").write_text(
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
        _make_request(root, "alice")
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
