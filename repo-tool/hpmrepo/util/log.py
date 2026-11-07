# -*- coding: utf-8 -*-
"""操作日志。

只记录操作、路径、结果，不记录 Token / 密码 / 私钥。
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOGGER: logging.Logger | None = None


def log_path() -> Path:
    home = Path.home()
    return home / ".hpm" / "logs" / "repo-tool.log"


def get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("hrm")
    logger.setLevel(logging.INFO)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            path, when="midnight", backupCount=7, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())
    _LOGGER = logger
    return logger


def log_action(action: str, detail: str = "") -> None:
    get_logger().info("%s %s", action, detail)
