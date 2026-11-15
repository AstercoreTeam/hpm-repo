# -*- coding: utf-8 -*-
"""轻量 i18n：词典放在 repo-tool/i18n/，用户语言设置放在 ~/.hpm/hrm.json。"""

from __future__ import annotations

import json
import os
from pathlib import Path

LANGUAGES = {"en": "English", "zh": "中文"}
DEFAULT_LANGUAGE = "en"
_current = DEFAULT_LANGUAGE
_en: dict[str, str] = {}
_zh: dict[str, str] = {}
_phrase_keys: list[str] = []
_cache: dict[tuple[str, str], str] = {}


def i18n_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "i18n"


def _load(name: str) -> dict[str, str]:
    path = i18n_dir() / f"{name}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def init(language: str | None = None) -> str:
    global _en, _zh, _phrase_keys, _current
    env = os.environ.get("HPM_LANG", "").strip().lower()
    if env in LANGUAGES:
        _current = env
    elif language in LANGUAGES:
        _current = language
    else:
        _current = DEFAULT_LANGUAGE
    _en = _load("en")
    _zh = _load("zh")
    _phrase_keys = sorted(_en.keys(), key=len, reverse=True)
    _cache.clear()
    return _current


def language() -> str:
    return _current


def set_language(language: str) -> str:
    global _current
    if language not in LANGUAGES:
        language = DEFAULT_LANGUAGE
    _current = language
    _cache.clear()
    return _current


def tr(text: str) -> str:
    if not text:
        return text
    cached = _cache.get((_current, text))
    if cached is not None:
        return cached
    result = _tr(text)
    _cache[(_current, text)] = result
    return result


def _tr(text: str) -> str:
    if _current == "zh":
        return _zh.get(text, text)
    exact = _en.get(text)
    if exact is not None:
        return exact
    # 动态拼接串按已知短语替换，尽量覆盖 "仓库: xxx" 这类组合
    out = text
    for key in _phrase_keys:
        if len(key) >= 2 and key in out:
            out = out.replace(key, _en[key])
    return out


def tr_lines(lines: list[str] | tuple[str, ...]) -> list[str]:
    return [tr(line) for line in lines]
