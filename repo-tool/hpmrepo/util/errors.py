# -*- coding: utf-8 -*-
"""异常类型。"""

from __future__ import annotations


class ToolError(Exception):
    """所有可展示给用户的错误的基类。"""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - 简单拼接
        return f"{self.message}（{self.hint}）" if self.hint else self.message


class ConfigError(ToolError):
    pass


class RepoError(ToolError):
    pass


class RoleError(RepoError):
    pass


class AuthError(ToolError):
    pass


class CryptoError(ToolError):
    pass


class KeyNotFound(CryptoError):
    pass


class PasswordError(CryptoError):
    pass


class RequestInvalid(ToolError):
    pass


class GitError(ToolError):
    pass


class UserCancel(ToolError):
    def __init__(self) -> None:
        super().__init__("已取消")
