import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox
from qt_lifecycle import qt_widget_lifecycle

from data_mask_studio.app import create_application
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.batch import BatchFileStatus
from data_mask_studio.field_rules import FieldRuleSet, FieldRuleRepository, ConfirmedFieldRules
from data_mask_studio.gui.field_rules import FieldRuleEditor, FieldRuleReviewDialog
from data_mask_studio.i18n import Translator
from data_mask_studio.profiles import ProfileRepository, ProfileService
from test_chinese_workflow import write_rows, read_rows
from test_field_rules import rules
from test_main_navigation import build_window


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    panel = window.batch_widget
    panel.retranslate(Translator("zh-CN"))
    panel._rule_repository = FieldRuleRepository(tmp_path / "field_rules.json")
    panel._rule_repository.save(rules(), expected=FieldRuleSet())
    first, second = tmp_path / "表一.csv", tmp_path / "表二.csv"
    write_rows(first, [["核算主体", "科目名称", "金额"], ["合成甲", "合成科目", "10.00"]])
    write_rows(second, [["备注", "公司名称"], ["明文备注", "合成甲"]])
    panel.add_paths([first, second])
    panel.output_field.setText(str(tmp_path / "output"))
    (tmp_path / "output").mkdir()
    panel.mode_combo.setCurrentIndex(1)
    return app, window, panel, (first, second)


def validate(app, panel):
    panel.validate_files()
    worker = panel._validation_worker
    assert worker is not None and worker.wait(10000)
    app.processEvents()
    assert panel._validation_worker is None


def confirm(monkeypatch, panel):
    def accept(dialog):
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        dialog.confirmation.setChecked(True)
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(FieldRuleReviewDialog, "exec", accept)
    panel.confirm_field_review()


def test_cross_table_ui_requires_review_before_start_and_processes(panel, monkeypatch):
    app, _, widget, paths = panel
    assert not widget.profile_combo.isVisible()
    assert widget.field_rule_mode()
    assert not widget.start_button.isEnabled()
    validate(app, widget)
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert widget.review_rules_button.isEnabled() and not widget.start_button.isEnabled()
    review = FieldRuleReviewDialog(widget.files, widget, widget._translator)
    assert review.table.rowCount() == 5
    assert review.table.item(3, 3).text() == "保留"
    assert review.table.item(3, 4).text() == "未匹配：保留明文"
    review.close()
    confirm(monkeypatch, widget)
    assert isinstance(widget._confirmed_rules, ConfirmedFieldRules)
    assert widget.start_button.isEnabled()
    widget.start_processing()
    worker = widget._processing_worker
    assert worker is not None and worker.wait(10000)
    app.processEvents()
    assert all(item.status is BatchFileStatus.COMPLETED for item in widget.files)
    first, second = [read_rows(item.output_path) for item in widget.files]
    assert first[1][0] == second[1][1]
    assert first[1][2] == "10.00" and second[1][0] == "明文备注"
    assert widget._confirmed_rules is None and not widget.start_button.isEnabled()


def test_cancelled_or_unchecked_review_does_not_approve(panel, monkeypatch):
    app, _, widget, _ = panel
    validate(app, widget)
    monkeypatch.setattr(FieldRuleReviewDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    widget.confirm_field_review()
    assert widget._confirmed_rules is None
    monkeypatch.setattr(FieldRuleReviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    widget.confirm_field_review()
    assert widget._confirmed_rules is None and not widget.start_button.isEnabled()


def test_no_match_is_visible_and_cannot_be_confirmed(panel):
    app, _, widget, paths = panel
    write_rows(paths[1], [["未知列", "金额"], ["合成甲", "20"]])
    validate(app, widget)
    assert widget.files[1].rule_error_id == "field_rules.error.no_match"
    assert "没有任何列命中" in widget.file_table.item(1, 4).text()
    review = FieldRuleReviewDialog(widget.files, widget, widget._translator)
    assert not review.confirmation.isEnabled()
    assert not review.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    review.close()


def test_file_changed_after_approval_is_rejected_in_worker(panel, monkeypatch):
    app, _, widget, paths = panel
    validate(app, widget)
    confirm(monkeypatch, widget)
    write_rows(paths[0], [["核算主体", "科目名称", "金额"], ["合成乙", "合成科目", "10.00"]])
    widget.start_processing()
    worker = widget._processing_worker
    assert worker is not None and worker.wait(10000)
    app.processEvents()
    assert "文件内容或表结构" in widget.status_label.text()
    assert not list((paths[0].parent / "output").iterdir())
    assert not widget.start_button.isEnabled()


def test_rule_changes_modes_and_environment_invalidate_approval(panel, monkeypatch):
    app, window, widget, _ = panel
    validate(app, widget)
    confirm(monkeypatch, widget)
    window._environment_maintained()
    assert widget._confirmed_rules is None and not widget.start_button.isEnabled()
    validate(app, widget)
    confirm(monkeypatch, widget)
    widget.mode_combo.setCurrentIndex(0)
    assert widget._confirmed_rules is None
    widget.mode_combo.setCurrentIndex(1)
    assert widget._requires_revalidation


def test_editor_import_preserves_existing_prefix_and_saved_rules(panel, tmp_path):
    _, _, widget, _ = panel
    profile_service = ProfileService(ProfileRepository(tmp_path / "profiles-import.json"))
    profile = profile_service.create("已使用的方案", [ColumnConfig("原主体列", True, "AAAA")])
    repository = FieldRuleRepository(tmp_path / "editor-rules.json")
    editor = FieldRuleEditor(repository, [profile], widget, Translator("zh-CN"))
    editor.import_profile()
    result = editor.read_rules()
    assert result.rules[0].prefix == "AAAA" and result.rules[0].aliases == ("原主体列",)
    editor.table.cellWidget(0, 2).setPlainText("原主体列\n公司名称\n核算主体")
    editor.save_rules()
    assert repository.load().rules[0].prefix == "AAAA"
    assert repository.load().rules[0].aliases == ("原主体列", "公司名称", "核算主体")


def test_retranslation_does_not_revoke_review_or_mutate_rules(panel, monkeypatch):
    app, _, widget, _ = panel
    validate(app, widget)
    confirm(monkeypatch, widget)
    before = widget._confirmed_rules, widget._rule_repository.path.read_bytes()
    widget.retranslate(Translator("en"))
    widget.retranslate(Translator("zh-CN"))
    assert before == (widget._confirmed_rules, widget._rule_repository.path.read_bytes())
    assert widget.start_button.isEnabled()
