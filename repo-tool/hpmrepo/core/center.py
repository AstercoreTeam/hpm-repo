# -*- coding: utf-8 -*-
"""HPM 开发者中心应用 API（仅私域管理员同步用）。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import hpm_home
from ..util.errors import ToolError

DEFAULT_API_BASE = "https://hpm.astercore.dpdns.org"
# 与 hpm-dev 编译期常量一致；可通过环境变量或 ~/.hpm/app.json 覆盖
DEFAULT_APP_ID = "1bdcb2b136d43431"
DEFAULT_APP_KEY = "2942c1a1b37c104914f4dd8f96a399bf"


class CenterError(ToolError):
    def __init__(self, message: str, hint: str = "", status: int = 0) -> None:
        super().__init__(message, hint)
        self.status = status


def _credentials() -> tuple[str, str, str]:
    app_id = os.environ.get("HPM_APP_ID", "")
    app_key = os.environ.get("HPM_APP_KEY", "")
    api_base = os.environ.get("HPM_API_BASE", "")
    path = hpm_home() / "app.json"
    if path.is_file() and (not app_id or not app_key):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            app_id = app_id or str(data.get("appId") or "")
            app_key = app_key or str(data.get("appKey") or "")
            api_base = api_base or str(data.get("apiBase") or "")
    return (
        app_id or DEFAULT_APP_ID,
        app_key or DEFAULT_APP_KEY,
        (api_base or DEFAULT_API_BASE).rstrip("/"),
    )


def call(action: str, access_token: str, payload: dict[str, Any] | None = None) -> Any:
    app_id, app_key, base = _credentials()
    if not access_token:
        raise CenterError("缺少 accessToken", "请重新登录开发者中心")
    body: dict[str, Any] = {"action": action, "accessToken": access_token}
    if payload is not None:
        body["payload"] = payload
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/api/webhooks/apps",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {app_key}",
            "Content-Type": "application/json",
            "User-Agent": "hrm/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise CenterError(
            f"开发者中心返回 HTTP {exc.code}", raw[:300], status=exc.code
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CenterError("无法连接开发者中心", str(exc)) from exc
    if status >= 400:
        raise CenterError(f"开发者中心返回 HTTP {status}", raw[:300], status=status)
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return raw


def _repo_base(repo_url: str) -> str:
    url = (repo_url or "").strip().rstrip("/")
    if not url:
        return ""
    for suffix in ("/index.json", "/official.crt"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    if "github.com/" in url and "raw.githubusercontent.com" not in url:
        rest = url.split("github.com/", 1)[1].strip("/")
        parts = rest.split("/")
        if len(parts) >= 2:
            return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/main"
    return url


def sync_developer(repo_url: str, root: Path, name: str, token: str) -> Any:
    base = _repo_base(repo_url)
    if not base:
        raise CenterError("缺少私域仓库地址", "请先在开发者中心确认 repoUrl")
    directory = Path(root) / name
    pub = directory / f"{name}.pub"
    sig = directory / f"{name}.pub.sig"
    if not pub.is_file() or not sig.is_file():
        raise CenterError(
            f"缺少 {name}.pub / .pub.sig",
            "旧版本签发的开发者证书没有公钥文件，需重新签发后同步",
        )
    payload = {
        "name": name,
        "pubUrl": f"{base}/{name}/{name}.pub",
        "pubSigUrl": f"{base}/{name}/{name}.pub.sig",
    }
    return call("update_developer", token, payload)


def sync_package(
    repo_url: str,
    package: str,
    version: str,
    entry: dict[str, Any],
    token: str,
) -> Any:
    base = _repo_base(repo_url)
    if not base:
        raise CenterError("缺少私域仓库地址", "请先在开发者中心确认 repoUrl")

    def url_of(value: str) -> str:
        return str(value or "").replace("{baseurl}", base)

    payload = {
        "name": package,
        "kind": str(entry.get("kind") or ""),
        "requiresJson": json.dumps(
            entry.get("requires") or {}, separators=(",", ":"), ensure_ascii=False
        ),
        "version": version,
        "url": url_of(entry.get("url", "")),
        "sigUrl": url_of(entry.get("sig_url", "")),
        "sha256": str(entry.get("sha256") or ""),
        "size": int(entry.get("size") or 0),
        "license": str(entry.get("license") or "MIT"),
    }
    return call("update_package", token, payload)


def delete_developer(name: str, token: str) -> Any:
    try:
        return call("delete_developer", token, {"name": name})
    except CenterError as exc:
        if exc.status == 404:
            return {"success": True, "deleted": 0, "message": "already absent"}
        raise


def delete_package(name: str, token: str) -> Any:
    try:
        return call("delete_package", token, {"name": name})
    except CenterError as exc:
        if exc.status == 404:
            return {"success": True, "deleted": 0, "message": "already absent"}
        raise
