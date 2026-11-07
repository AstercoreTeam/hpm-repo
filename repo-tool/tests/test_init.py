# -*- coding: utf-8 -*-
"""初始化与收尾流程测试（不启动 TUI）。"""

from __future__ import annotations

import json

from hpmrepo.core import crypto, developers as dev_mod, index as index_mod, keys as keys_mod, revoke
from hpmrepo.repo import detect
from hpmrepo.views import init_repo


class FakeApp:
    def __init__(self, ctx, password: str = "", cached: dict | None = None):
        self.ctx = ctx
        self.dev = None
        self.password = password
        self._cache = cached or {}
        self.messages = []
        self.toasts = []

    def busy(self, *_args, **_kwargs):
        pass

    def toast(self, message, level="info", seconds=4.0):
        self.toasts.append((message, level))

    def info(self, title, lines=None, level="info"):
        self.messages.append((title, lines))

    def error(self, message, hint=""):
        raise AssertionError(f"unexpected error: {message} / {hint}")

    def input(self, title, prompt, *, default="", mask=False, hint=""):
        return self.password

    def cached_key(self, path):
        return self._cache.get(path)

    def cache_key(self, path, key):
        self._cache[path] = key

    def home(self):
        pass


def test_init_execute(repo, ca, monkeypatch):
    ctx = detect(repo)
    app = FakeApp(ctx)
    view = init_repo.InitRepoView(app)

    # 绕过交互，直接执行初始化
    view._execute("test", "dev@example.com", "", "password123")

    key_path = keys_mod.official_key_path("test")
    assert key_path.is_file()
    assert crypto.private_key_is_encrypted(key_path)
    assert (repo / "official.pub").is_file()
    assert (repo / "index.json").is_file()
    assert (repo / "revoke.json").is_file()
    assert (repo / ".git").is_dir()

    doc = json.loads((repo / "official.request.json").read_text(encoding="utf-8"))
    request = dev_mod.validate_request_data(doc, repo)
    assert request.kind == "domain"
    assert request.name == "test"
    assert request.san == "hpm://domain/test"

    # 用测试 CA 扮演官方，签发域证书
    ca_key, ca_cert, ca_pem = ca
    issuer = repo / "issuer.crt"
    issuer.write_bytes(ca_pem)
    monkeypatch.setenv("HPM_OFFICIAL_CERT", str(issuer))
    cert_pem = crypto.issue_certificate_from_csr(
        ca_cert,
        ca_key,
        crypto.load_csr_bytes(request.csr_pem.encode()),
        "HPM Domain test",
        request.san,
        profile="domain",
    )
    (repo / "official.crt").write_bytes(cert_pem)

    role = detect(repo)
    assert role.role.value == "private"

    # 收尾：签名索引与吊销列表
    key = crypto.load_private_key(key_path, "password123")
    app = FakeApp(role, cached={str(key_path): key})
    monkeypatch.setattr(init_repo, "commit_ui", lambda *a, **k: True)
    finish = init_repo.FinishInitView(app)
    finish._finish()

    ok, detail = index_mod.verify_index(repo)
    assert ok, detail
    # revoke.json 在初始化阶段是空列表，收尾时补 issuer 并签名
    assert (repo / "revoke.json.sig").is_file()
    ok, detail = revoke.verify(repo)
    assert ok, detail
