# -*- coding: utf-8 -*-
"""按私钥文件存在性判定人员身份（不验证私钥内容）。"""

from __future__ import annotations

from enum import Enum

from ..repo import RepoContext, Role
from . import keys


class Identity(str, Enum):
    UNREGISTERED = "unregistered"
    DEVELOPER = "developer"
    PRIVATE_ADMIN = "private_admin"
    OFFICIAL_ADMIN = "official_admin"

    @property
    def label(self) -> str:
        return {
            Identity.UNREGISTERED: "未注册",
            Identity.DEVELOPER: "普通开发者",
            Identity.PRIVATE_ADMIN: "私域管理员",
            Identity.OFFICIAL_ADMIN: "HPM 官方管理员",
        }[self]


def detect(ctx: RepoContext, developer: str = "") -> Identity:
    """优先级：官方管理员 > 私域管理员 > 普通开发者 > 未注册。"""
    if keys.has_official_admin_key():
        return Identity.OFFICIAL_ADMIN

    # 官方仓库只认官方私钥；域私钥不授予官方仓库管理权
    if ctx.role is not Role.OFFICIAL:
        if keys.has_domain_admin_key(ctx.domain):
            return Identity.PRIVATE_ADMIN

    if keys.has_developer_key(developer) or keys.has_developer_key():
        return Identity.DEVELOPER

    return Identity.UNREGISTERED
