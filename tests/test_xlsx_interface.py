import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.xlsx_import import prepare_tabular_paths
from data_mask_studio.i18n import Translator
from test_main_navigation import build_window
from test_xlsx_import import fixture_book


def test_conversion_dialog_converts_all_sheets_and_routes_to_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    window.retranslate(Translator("zh-CN"))
    source = fixture_book(tmp_path / "多店.xlsx")
    output = tmp_path / "converted"
    output.mkdir()
    monkeypatch.setattr("data_mask_studio.gui.xlsx_import.QFileDialog.getExistingDirectory", lambda *args: str(output))
    window.load_csv(str(source))
    app.processEvents()
    assert len(window.batch_widget.files) == 2
    assert window.current_page_index() == window.page_index(window.batch_widget)
    assert all(item.path.suffix == ".csv" for item in window.batch_widget.files)


def test_single_sheet_opens_in_individual_masking(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    create_application([])
    window = build_window(tmp_path)
    source = fixture_book(tmp_path / "单表.xlsx", extra_sheet=False)
    output = tmp_path / "converted"
    output.mkdir()
    monkeypatch.setattr("data_mask_studio.gui.xlsx_import.QFileDialog.getExistingDirectory", lambda *args: str(output))
    window.load_csv(str(source))
    assert window.config_table.rowCount() == 6
    assert window.config_table.item(0, 1).text() == "日期"


def test_returned_excel_routes_to_batch_restoration(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    source = fixture_book(tmp_path / "返回.xlsx")
    output = tmp_path / "converted"
    output.mkdir()
    monkeypatch.setattr("data_mask_studio.gui.xlsx_import.QFileDialog.getExistingDirectory", lambda *args: str(output))
    assert window.file_restoration_widget.load_file(source)
    app.processEvents()
    assert len(window.batch_restoration_widget.files) == 2
    assert window.current_page_index() == window.page_index(window.batch_restoration_widget)


def test_cancel_folder_selection_keeps_previous_files(tmp_path, monkeypatch):
    create_application([])
    source = fixture_book(tmp_path / "报表.xlsx")
    monkeypatch.setattr("data_mask_studio.gui.xlsx_import.QFileDialog.getExistingDirectory", lambda *args: "")
    assert prepare_tabular_paths([source], None, Translator("zh-CN")) == []
