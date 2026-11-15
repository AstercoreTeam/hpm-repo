# -*- coding: utf-8 -*-
"""语言设置与词典测试。"""

from __future__ import annotations

from pathlib import Path

from hpmrepo import config, i18n


def test_default_language_is_english_and_saved_locally(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HPM_HOME", str(home))
    monkeypatch.delenv("HPM_LANG", raising=False)
    assert config.language_setting() == "en"
    config.save_language("zh")
    path = home / "hrm.json"
    assert path.is_file()
    assert config.language_setting() == "zh"
    assert not (tmp_path / "repo" / "hrm.json").exists()


def test_dictionary_translation(monkeypatch):
    monkeypatch.delenv("HPM_LANG", raising=False)
    i18n.init("en")
    assert i18n.tr("管理包") == "Packages"
    assert i18n.tr("设置 / 诊断") == "Settings / Diagnostics"
    i18n.init("zh")
    assert i18n.tr("管理包") == "管理包"
