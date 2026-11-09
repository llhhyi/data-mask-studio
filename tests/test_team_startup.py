from types import SimpleNamespace

import pytest

from data_mask_studio import app as application_module
from data_mask_studio.preferences import LanguagePreference


@pytest.mark.parametrize("saved", [False, True])
def test_frozen_first_start_is_chinese_and_preserves_existing_preference(tmp_path, monkeypatch, saved):
    path = tmp_path / "preferences.json"
    if saved:
        path.write_text('{"schema_version": 1, "language": "en"}', encoding="utf-8")
    preferences = SimpleNamespace(path=path, load=lambda: SimpleNamespace(preferences=SimpleNamespace(language=LanguagePreference.ENGLISH)),
                                  resolve_language=lambda _preference: "en")
    monkeypatch.setattr(application_module, "PreferencesService", lambda: preferences)
    monkeypatch.setattr(application_module.sys, "frozen", True, raising=False)
    observed = []
    def application(*, translator):
        observed.append(translator.locale)
        return SimpleNamespace(exec=lambda: 0)
    monkeypatch.setattr(application_module, "create_application", application)
    monkeypatch.setattr(application_module, "MainWindow", lambda **kwargs: SimpleNamespace(show=lambda: None))
    assert application_module.run() == 0
    assert observed == ["en" if saved else "zh-CN"]
    assert path.exists() == saved
