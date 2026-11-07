# -*- coding: utf-8 -*-
"""文件读写辅助。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes | str, mode: int = 0o644) -> None:
    """先写临时文件，再原子替换，避免半截文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def ensure_private(path: Path) -> None:
    """把私钥权限收紧到 0600（POSIX）。"""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_private_mode(path: Path) -> bool:
    try:
        return (os.stat(path).st_mode & 0o077) == 0
    except OSError:
        return True


def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"
