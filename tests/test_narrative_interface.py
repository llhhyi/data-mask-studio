import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
import pytest
from PySide6.QtWidgets import QApplication

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import (
    BatchRestorationService, BatchRestorationOptions, BatchRestorationStatus, add_files,
)
from data_mask_studio.gui.file_restoration_widget import FileRestorationWidget
from data_mask_studio.gui.narrative_restoration_widget import NarrativeRestorationWidget
from data_mask_studio.i18n import Translator
from test_narrative_restoration import case, CODE, SECOND, MISSING, VALUE, word_bytes
from test_main_navigation import build_window


def finish(app, widget):
    worker = widget._worker
    assert worker is not None and worker.wait(10000)
    app.processEvents()
    assert widget._worker is None


def test_paste_restore_separate_result_copy_manual_and_invalidate(case):
    app = create_application([])
    service, vault = case
    widget = NarrativeRestorationWidget(lambda: vault, paste=True, translator=Translator('zh-CN'))
    QApplication.clipboard().setText('unchanged clipboard')
    widget.mode_combo.setCurrentIndex(widget.mode_combo.findData('plain'))
    widget.input.setPlainText(f'结论：{CODE}')
    widget.restore_text()
    finish(app, widget)
    assert widget.input.toPlainText() == f'结论：{CODE}'
    assert widget.output.toPlainText() == f'结论：{VALUE}'
    assert QApplication.clipboard().text() == 'unchanged clipboard'
    widget.copy_result()
    assert QApplication.clipboard().text() == f'结论：{VALUE}'
    widget.input.setPlainText(MISSING)
    assert not widget.output.toPlainText() and not widget.copy_button.isEnabled()
    widget.restore_text()
    finish(app, widget)
    assert not widget.output.toPlainText() and MISSING in widget.summary.toPlainText()
    assert '停止输出' in widget.status_label.text()
    widget.close()


def test_paste_json_mode_and_markdown_mode_behave_differently(case):
    app = create_application([])
    widget = NarrativeRestorationWidget(lambda: case[1], paste=True, translator=Translator('zh-CN'))
    widget.mode_combo.setCurrentIndex(widget.mode_combo.findData('json'))
    widget.input.setPlainText(json.dumps({'主体': CODE, '人数': 2}, ensure_ascii=False))
    widget.restore_text()
    finish(app, widget)
    assert json.loads(widget.output.toPlainText()) == {'主体': VALUE, '人数': 2}
    widget.mode_combo.setCurrentIndex(widget.mode_combo.findData('markdown'))
    assert not widget.output.toPlainText()
    widget.input.setPlainText(f'`{CODE}`\n\n{SECOND}')
    widget.restore_text()
    finish(app, widget)
    assert widget.output.toPlainText() == f'`{CODE}`\n\n合成人员乙'
    assert '保留' in widget.status_label.text()
    result = widget.output.toPlainText()
    widget.retranslate(Translator('en'))
    assert widget.output.toPlainText() == result
    widget.close()


def test_stale_worker_result_never_populates_changed_input(case):
    app = create_application([])
    widget = NarrativeRestorationWidget(lambda: case[1], paste=True)
    widget.input.setPlainText(CODE)
    widget.restore_text()
    widget.input.setPlainText(SECOND)
    finish(app, widget)
    assert not widget.output.toPlainText() and not widget.copy_button.isEnabled()
    widget.close()


@pytest.mark.parametrize('extension', ['.md', '.markdown', '.docx', '.json'])
def test_unified_file_restoration_dispatches_new_formats_and_exports(case, tmp_path, extension):
    app = create_application([])
    unified = FileRestorationWidget(lambda: case[1], translator=Translator('zh-CN'))
    source, target = tmp_path / ('AI' + extension), tmp_path / ('result' + extension)
    source.write_bytes(word_bytes() if extension == '.docx' else (json.dumps({'name': CODE}) if extension == '.json' else CODE).encode())
    assert unified.load_file(source)
    widget = unified.document_widget
    assert unified.stack.currentWidget() is widget and not unified.csv_widget._inspection
    widget.start_analysis()
    finish(app, widget)
    assert widget._result.found_codes > 0
    widget.restore_to(target)
    finish(app, widget)
    assert target.exists() and widget._last_output_path == target
    unified.close()


def test_mixed_batch_restoration_includes_all_existing_and_new_formats(case, tmp_path):
    service = BatchRestorationService(lambda: case[1])
    paths = []
    for name, raw in [('one.csv', f'name,note\n{CODE},common\n'.encode()),
                      ('two.html', f'<p>{SECOND}</p>'.encode()), ('three.md', CODE.encode()),
                      ('four.json', json.dumps({'name': CODE}).encode()), ('five.docx', word_bytes())]:
        path = tmp_path / name
        path.write_bytes(raw)
        paths.append(path)
    files = []
    assert add_files(files, paths) == 5
    service.analyze_files(files)
    assert all(item.status is BatchRestorationStatus.COMPATIBLE for item in files)
    for item in files:
        for column in item.columns:
            column.selected = column.is_candidate
    output = tmp_path / 'results'
    output.mkdir()
    summary = service.restore_files(files, output, BatchRestorationOptions())
    assert summary.completed_files == 5 and summary.error_files == 0
    assert {p.suffix for p in output.iterdir()} == {'.csv', '.html', '.md', '.json', '.docx'}
    assert all(item.status is BatchRestorationStatus.COMPLETED for item in files)


def test_main_window_adds_text_navigation_and_environment_reset_clears_text(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    app = create_application([])
    window = build_window(tmp_path)
    window.retranslate(Translator('zh-CN'))
    panel = window.text_restoration_widget
    assert window.navigation.buttons[window.page_index(panel)].text() == '文本还原'
    panel.input.setPlainText(CODE)
    panel.output.setPlainText('sensitive result')
    window._environment_maintained()
    assert not panel.input.toPlainText() and not panel.output.toPlainText()
    assert window._prepare_restore()
    window.close()


def test_unknown_document_stops_batch_even_with_legacy_keep_option(case, tmp_path):
    from data_mask_studio.batch_restoration import BatchMissingCodePolicy
    from data_mask_studio.gui.batch_restoration_widget import BatchRestorationWidget
    app = create_application([])
    source, second = tmp_path / 'unknown.json', tmp_path / 'known.md'
    source.write_text(json.dumps({'name': MISSING}), encoding='utf-8')
    second.write_text(CODE, encoding='utf-8')
    files = []
    add_files(files, [source, second])
    service = BatchRestorationService(lambda: case[1])
    service.analyze_files(files)
    widget = BatchRestorationWidget(lambda: case[1], lambda: True, translator=Translator('zh-CN'))
    widget.files = files
    output = tmp_path / 'output'
    output.mkdir()
    widget.output_field.setText(str(output))
    assert not widget._can_start()
    widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(BatchMissingCodePolicy.ABORT_FILE.value))
    assert widget._can_start() and widget.start_button.isEnabled()
    widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(BatchMissingCodePolicy.ABORT_BATCH.value))
    assert not widget._can_start() and not widget.start_button.isEnabled()
    widget.missing_policy_combo.setCurrentIndex(widget.missing_policy_combo.findData(BatchMissingCodePolicy.KEEP.value))
    summary = service.restore_files(files, output, BatchRestorationOptions())
    assert summary.completed_files == 0 and summary.error_files == 1 and summary.skipped_files == 1
    assert not list(output.iterdir()) and MISSING in widget._result_text(files[0])
    widget.close()
