# -*- coding: utf-8 -*-
"""子进程辅助。"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def summary(self, limit: int = 400) -> str:
        text = (self.out or self.err or "").strip()
        if len(text) > limit:
            text = text[:limit] + "…"
        return text


def run(
    args: list[str],
    cwd: Path | None = None,
    timeout: int | None = 120,
    env: dict[str, str] | None = None,
) -> RunResult:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return RunResult(proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError:
        return RunResult(127, "", f"找不到命令: {args[0]}")
    except subprocess.TimeoutExpired:
        return RunResult(124, "", f"命令超时: {' '.join(args)}")


def which(name: str) -> str | None:
    return shutil.which(name)


def has_git() -> bool:
    return which("git") is not None
