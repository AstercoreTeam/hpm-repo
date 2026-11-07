# -*- coding: utf-8 -*-
"""私钥定位。"""

from __future__ import annotations

from pathlib import Path

from . import crypto


def official_candidates(root: Path) -> list[Path]:
    root = Path(root)
    return [
        root / "official.key",
        Path.home() / ".hpm" / "keys" / "official.key",
    ]


def developer_candidates(root: Path, developer: str) -> list[Path]:
    root = Path(root)
    return [
        root / developer / f"{developer}.key",
        Path.home() / ".hpm" / "keys" / developer / f"{developer}.key",
    ]


def find_official(root: Path) -> Path | None:
    for path in official_candidates(root):
        if path.is_file():
            return path
    return None


def find_developer(root: Path, developer: str) -> Path | None:
    for path in developer_candidates(root, developer):
        if path.is_file():
            return path
    return None


def key_looks_valid(path: Path) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "PRIVATE KEY" in text


def key_is_encrypted(path: Path) -> bool:
    return crypto.private_key_is_encrypted(path)
