# -*- coding: utf-8 -*-
"""身份判定与开发者中心同步 payload 测试。"""

from __future__ import annotations

from pathlib import Path

from hpmrepo import config
from hpmrepo.core import center, identity, keys
from hpmrepo.core.identity import Identity
from hpmrepo.repo import detect


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("dummy", encoding="utf-8")
    return path


def test_identity_by_key_files(repo, ca):
    ctx = detect(repo)
    assert identity.detect(ctx, "") is Identity.UNREGISTERED

    _touch(keys.developer_key_path("alice"))
    assert identity.detect(ctx, "alice") is Identity.DEVELOPER
    # 不依赖 dev.json 中的名字：只要存在开发者私钥就是普通开发者
    assert identity.detect(ctx, "bob") is Identity.DEVELOPER

    _touch(keys.official_key_path("example"))
    ctx = detect(repo)
    # 当前仓库未初始化，域私钥视为私域管理员
    assert identity.detect(ctx, "alice") is Identity.PRIVATE_ADMIN

    _touch(keys.official_key_path(""))
    assert identity.detect(ctx, "alice") is Identity.OFFICIAL_ADMIN


def test_domain_key_does_not_admin_official_repo(repo):
    source = Path(__file__).resolve().parents[2]
    (repo / "official.crt").write_bytes((source / "official.crt").read_bytes())
    _touch(keys.official_key_path("example"))
    ctx = detect(repo)
    assert ctx.role.value == "official"
    assert identity.detect(ctx, "") is Identity.UNREGISTERED


def test_center_sync_payload(repo, monkeypatch):
    captured = {}

    def fake_call(action, token, payload=None):
        captured.update(action=action, token=token, payload=payload)
        return {"success": True}

    monkeypatch.setattr(center, "call", fake_call)
    entry = {
        "kind": "driver",
        "url": "{baseurl}/alice/demo-ch552-1.0.0.tar.gz",
        "sig_url": "{baseurl}/alice/demo-ch552-1.0.0.tar.gz.sig",
        "sha256": "abc",
        "size": 12,
        "license": "MIT",
        "requires": {"pin": {"type": "gpio_out"}},
    }
    center.sync_package(
        "https://raw.githubusercontent.com/a/b/main",
        "demo-ch552",
        "1.0.0",
        entry,
        "token",
    )
    assert captured["action"] == "update_package"
    assert captured["payload"]["url"].startswith(
        "https://raw.githubusercontent.com/a/b/main/alice/"
    )
    assert captured["payload"]["requiresJson"] == '{"pin":{"type":"gpio_out"}}'

    dev_dir = repo / "alice"
    dev_dir.mkdir()
    (dev_dir / "alice.pub").write_text("pub", encoding="utf-8")
    (dev_dir / "alice.pub.sig").write_text("sig", encoding="utf-8")
    center.sync_developer(
        "https://example.com/hpm", repo, "alice", "token"
    )
    assert captured["action"] == "update_developer"
    assert captured["payload"]["pubUrl"] == "https://example.com/hpm/alice/alice.pub"


def test_center_delete_actions(monkeypatch):
    captured = {}

    def fake_call(action, token, payload=None):
        captured.update(action=action, token=token, payload=payload)
        return {"success": True, "deleted": 1}

    monkeypatch.setattr(center, "call", fake_call)
    center.delete_developer("alice", "token")
    assert captured["action"] == "delete_developer"
    assert captured["payload"] == {"name": "alice"}
    center.delete_package("demo", "token")
    assert captured["action"] == "delete_package"
    assert captured["payload"] == {"name": "demo"}


def test_center_delete_tolerates_404(monkeypatch):
    def fake_call(action, token, payload=None):
        raise center.CenterError("not found", "404", status=404)

    monkeypatch.setattr(center, "call", fake_call)
    assert center.delete_developer("gone", "token")["deleted"] == 0
    assert center.delete_package("gone", "token")["deleted"] == 0
