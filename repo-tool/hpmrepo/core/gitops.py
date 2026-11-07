# -*- coding: utf-8 -*-
"""Git 操作。只调用系统 git，不实现协议、不处理凭据。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..util.errors import GitError
from ..util.shell import RunResult, has_git, run


@dataclass
class GitStatus:
    available: bool
    is_repo: bool
    branch: str = ""
    dirty: int = 0
    ahead: int = 0
    behind: int = 0
    remotes: str = ""
    error: str = ""

    @property
    def clean(self) -> bool:
        return self.dirty == 0


def _git(root: Path, *args: str, timeout: int = 60, no_prompt: bool = False) -> RunResult:
    env = None
    if no_prompt:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return run(["git", *args], cwd=Path(root), timeout=timeout, env=env)


def available() -> bool:
    return has_git()


def is_repo(root: Path) -> bool:
    if not available():
        return False
    result = _git(root, "rev-parse", "--is-inside-work-tree")
    return result.ok and result.out.strip() == "true"


def status(root: Path) -> GitStatus:
    if not available():
        return GitStatus(False, False, error="未安装 git")
    if not is_repo(root):
        return GitStatus(True, False, error="当前目录不是 Git 仓库")

    branch_result = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_result.out.strip() if branch_result.ok else ""

    porcelain = _git(root, "status", "--porcelain=v1")
    dirty = len([ln for ln in porcelain.out.splitlines() if ln.strip()])

    ahead = behind = 0
    counts = _git(root, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if counts.ok:
        parts = counts.out.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])

    remote_result = _git(root, "remote", "-v")
    remotes = remote_result.out.strip()

    return GitStatus(
        True, True, branch=branch, dirty=dirty, ahead=ahead, behind=behind,
        remotes=remotes,
    )


def init(root: Path, branch: str = "main") -> None:
    if is_repo(root):
        return
    result = _git(root, "init", "-b", branch)
    if not result.ok:
        raise GitError("git init 失败", result.summary())


def add(root: Path, paths: list[str]) -> None:
    if not paths:
        return
    result = _git(root, "add", "--", *paths)
    if not result.ok:
        raise GitError("git add 失败", result.summary())


def commit(root: Path, message: str) -> RunResult:
    return _git(
        root,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        message,
        timeout=120,
        no_prompt=True,
    )


def add_remote(root: Path, name: str, url: str) -> None:
    existing = _git(root, "remote", "get-url", name)
    if existing.ok:
        result = _git(root, "remote", "set-url", name, url)
        if not result.ok:
            raise GitError("设置远程地址失败", result.summary())
        return
    result = _git(root, "remote", "add", name, url)
    if not result.ok:
        raise GitError("添加远程地址失败", result.summary())


def remote_url(root: Path, name: str = "origin") -> str:
    result = _git(root, "remote", "get-url", name)
    return result.out.strip() if result.ok else ""


def push(root: Path, remote: str = "origin", branch: str = "") -> RunResult:
    args = ["push", remote]
    if branch:
        args.append(branch)
    return _git(root, *args, timeout=300, no_prompt=True)


def pull_rebase(root: Path) -> RunResult:
    return _git(root, "pull", "--rebase", timeout=300, no_prompt=True)


def changed_paths(root: Path) -> list[str]:
    result = _git(root, "status", "--porcelain=v1")
    if not result.ok:
        return []
    paths: list[str] = []
    for line in result.out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths


def tracked(root: Path, path: str) -> bool:
    result = _git(root, "ls-files", "--error-unmatch", "--", path)
    return result.ok


def is_ignored(root: Path, path: str) -> bool:
    result = _git(root, "check-ignore", "-q", "--", path)
    return result.ok


def last_commit(root: Path) -> str:
    result = _git(root, "log", "-1", "--pretty=%h %s")
    return result.out.strip() if result.ok else ""
