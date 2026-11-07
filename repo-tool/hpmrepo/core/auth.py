# -*- coding: utf-8 -*-
"""登录委托：探测 hpm-dev / hpm 的 login 子命令。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..util.shell import which


@dataclass
class LoginCommand:
    argv: list[str]
    label: str


def detect() -> LoginCommand | None:
    override = os.environ.get("HPM_DEV_BIN")
    if override and which(override):
        return LoginCommand([override, "login"], override)

    hpm_dev = which("hpm-dev")
    if hpm_dev:
        return LoginCommand([hpm_dev, "login"], "hpm-dev login")

    hpm = which("hpm")
    if hpm:
        # 主程序可能没有 login 子命令，交给调用方处理失败
        return LoginCommand([hpm, "login"], "hpm login")
    return None


def hint() -> str:
    return (
        "未找到可用的登录命令。请安装 hpm-dev（或把 HPM_DEV_BIN 指向它），"
        "然后运行 hpm-dev login。"
    )
