"""Validate the installed Chinese UI against its report workflow."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qt_lifecycle import qt_widget_lifecycle

from data_mask_studio import app as application_module
from data_mask_studio.app import create_application
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.profiles import ProfileRepository, ProfileService
from test_chinese_workflow import FixedKeyProvider, write_rows, read_rows
from test_main_navigation import FakeProtector
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.vault import VaultCipher, VaultRepository


def test_entry_point_applies_saved_chinese_language(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    service = PreferencesService()
    assert service.save_language("zh-CN").error is None
    calls = []

    class Application:
        def exec(self):
            return 0

    def create(*, translator):
        calls.append(translator.locale)
        return Application()

    class Window:
        def __init__(self, *, preferences_service, translator):
            assert preferences_service.load().preferences.language.value == "zh-CN"
            calls.append(translator.text("navigation.mask_file"))

        def show(self):
            calls.append("shown")

    monkeypatch.setattr(application_module, "create_application", create)
    monkeypatch.setattr(application_module, "MainWindow", Window)
    assert application_module.run() == 0
    assert calls == ["zh-CN", "文件脱敏", "shown"]


def test_chinese_window_generates_csv_with_background_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    local = tmp_path / "environment"
    local.mkdir()
    paths = EnvironmentPaths(local, local / "secret.key", local / "vault_key.dpapi",
                             local / "vault.db", local / "profiles.json")
    repository = VaultRepository(paths.vault_database_path, VaultCipher(b"V" * 32))
    application = create_application([])
    window = MainWindow(
        key_provider=FixedKeyProvider(), vault_repository_factory=lambda: repository,
        profile_service=ProfileService(ProfileRepository(paths.profiles_path)),
        backup_paths=paths, vault_key_provider=FixedKeyProvider(),
        data_protector=FakeProtector(), translator=Translator("zh-CN"),
    )
    source = tmp_path / "中文报表.csv"
    write_rows(source, [["供应商名称", "员工姓名", "金额"], ["合成供应商甲", "合成人员张三", "1234.50"]])
    window.load_csv(str(source))
    window.show()
    application.processEvents()
    assert [b.text() for b in window.navigation.buttons][:4] == ["文件脱敏", "批量脱敏", "文件还原", "批量还原"]
    assert window.anonymization_widget.select_button.text() == "选择 CSV / Excel"
    for index, prefix in enumerate(("SUPPLIER", "PERSON")):
        window._action_fields[index].setCurrentIndex(window._action_fields[index].findData(ColumnAction.MASK.value))
        window._prefix_fields[index].setText(prefix)
    window.validate_button.click()
    assert window.generate_button.isEnabled()
    assert "配置有效" in window.status_label.text()
    destination = tmp_path / "中文报表-输出.csv"
    window.anonymization_widget._start_processing(destination, overwrite=False)
    worker = window.anonymization_widget._worker
    assert worker is not None and worker.wait(5000)
    application.processEvents()
    assert "已成功生成脱敏 CSV" in window.status_label.text()
    assert read_rows(destination)[1][2] == "1234.50"
    assert read_rows(destination)[1][0].startswith("SUPPLIER-")
    window.set_current_page(window.page_index(window.settings_widget))
    application.processEvents()
    assert window.settings_widget.language_combo.findData("zh-CN") >= 0
    window.close()
