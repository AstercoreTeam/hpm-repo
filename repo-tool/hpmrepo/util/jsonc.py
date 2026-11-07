# -*- coding: utf-8 -*-
"""JSON 读写与规范化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """签名用的规范化 JSON：键排序、紧凑分隔、保留 Unicode。"""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
