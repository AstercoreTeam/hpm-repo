# -*- coding: utf-8 -*-
"""用户级配置：~/.hpm/dev.json（只读）。

路径规则与 hpm-dev 的 dev_config.cpp 保持一致：
$HPM_HOME/dev.json，否则 $HOME/.hpm/dev.json。
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .util.errors import ConfigError
from .util.jsonc import load_json


@dataclass
class DevConfig:
    access_token: str = ""
    developer: str = ""
    expires_at: str = ""
    github_url: str = ""
    repo_url: str = ""
    path: Path | None = None


TokenState = Literal["missing", "expired", "valid"]


@dataclass
class TokenStatus:
    state: TokenState
    expires_at: dt.datetime | None = None
    seconds_left: int = 0

    @property
    def ok(self) -> bool:
        return self.state == "valid"

    def label(self) -> str:
        if self.state == "missing":
            return "未登录"
        if self.state == "expired":
            when = (
                self.expires_at.astimezone().strftime("%Y-%m-%d %H:%M")
                if self.expires_at
                else "未知时间"
            )
            return f"已过期（{when}）"
        hours = max(1, self.seconds_left // 3600)
        return f"有效（剩余 {hours} 小时）"


def hpm_home() -> Path:
    env = os.environ.get("HPM_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".hpm"


def dev_json_path() -> Path:
    return hpm_home() / "dev.json"


def load_dev() -> DevConfig | None:
    path = dev_json_path()
    if not path.is_file():
        return None
    try:
        data = load_json(path)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"读取 dev.json 失败: {path}", str(exc)) from exc
    if not isinstance(data, dict):
        raise ConfigError("dev.json 格式错误", "根节点必须是对象")
    return DevConfig(
        access_token=str(data.get("accessToken") or ""),
        developer=str(data.get("developer") or ""),
        expires_at=str(data.get("expiresAt") or ""),
        github_url=str(data.get("githubUrl") or ""),
        repo_url=str(data.get("repoUrl") or ""),
        path=path,
    )


def parse_iso(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def token_status(dev: DevConfig | None) -> TokenStatus:
    if dev is None or not dev.access_token:
        return TokenStatus("missing")
    expires = parse_iso(dev.expires_at)
    if expires is None:
        # 有 Token 但没有可解析的过期时间：按已过期处理，提示重新登录
        return TokenStatus("expired", None, 0)
    now = dt.datetime.now(dt.timezone.utc)
    seconds = int((expires - now).total_seconds())
    if seconds <= 0:
        return TokenStatus("expired", expires, 0)
    return TokenStatus("valid", expires, seconds)
