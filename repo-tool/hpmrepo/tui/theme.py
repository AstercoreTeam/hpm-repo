# -*- coding: utf-8 -*-
"""颜色与主题。"""

from __future__ import annotations

import os

# 256 色编号
FG = {
    "default": None,
    "text": 252,
    "muted": 245,
    "dim": 240,
    "cyan": 51,
    "blue": 75,
    "green": 42,
    "yellow": 221,
    "red": 203,
    "magenta": 177,
    "white": 255,
    "black": 16,
}
BG = {
    "default": None,
    "panel": 235,
    "panel_alt": 236,
    "select": 24,
    "header": 234,
    "footer": 236,
    "modal": 237,
    "danger": 52,
    "success": 22,
}


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") in ("dumb", ""):
        return False
    return True
