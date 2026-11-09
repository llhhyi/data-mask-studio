"""Shared local file and paste workflows; clipboard access requires a button click."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from data_mask_studio.gui.components.presentation import set_button_role, set_feedback_state
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.i18n import Translator
from data_mask_studio.narrative_restoration import (
    NarrativeCancelled, NarrativeError, NarrativeRestorationService,
)


class NarrativeWorker(QThread):
    completed = Signal(object)
    failed = Signal(object)
    cancelled = Signal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.cancellation = Event()

    def request_cancel(self):
        self.cancellation.set()

    def run(self):
        try:
            result = self.operation(self.cancellation.is_set)
        except NarrativeCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)


def result_text(result, translator):
    text = translator.text("narrative.summary", total=result.total_occurrences,
                           found=result.found_codes, missing=result.missing_codes,
                           restored=result.restored_occurrences,
                           skipped=result.skipped_occurrences, invalid=result.invalid_occurrences)
    if result.missing:
        text += "\n" + translator.text("narrative.missing_list") + "\n" + "\n".join(result.missing)
    if result.skipped:
        text += "\n" + translator.text("narrative.skipped_list") + "\n" + "\n".join(result.skipped)
    if result.image_count:
        text += "\n" + translator.text("narrative.images", count=result.image_count)
    if result.output_path:
        text += "\n" + str(result.output_path)
    return text


class NarrativeRestorationWidget(QWidget):
    file_loaded = Signal()
    busy_changed = Signal(bool)

    def __init__(self, repository_factory, *, translator=None, file_selector=None, paste=False):
        super().__init__()
        self._translator = translator or Translator("pt-BR")
        self.service = NarrativeRestorationService(repository_factory)
        self.paste = paste
        self._worker = None
        self._inspection = None
        self._last_output_path = None
        self._result = None
        self._revision = 0
        self._operation_revision = 0
        self._status_id = "narrative.initial_text" if paste else "narrative.initial_file"
        self._operation = None

        self.select_button = QPushButton()
        self.select_button.clicked.connect(self.paste_clipboard if paste else file_selector)
        self.mode_combo = ScrollSafeComboBox()
        for mode in ("markdown", "plain", "json"):
            self.mode_combo.addItem("", mode)
        self.mode_combo.currentIndexChanged.connect(self.invalidate)
        self.path_label = QLabel()
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setWordWrap(True)
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.help_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.input = QPlainTextEdit()
        self.input.setMaximumBlockCount(0)
        self.input.textChanged.connect(self.invalidate)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.input_label, self.output_label = QLabel(), QLabel()
        self.input_label.setBuddy(self.input)
        self.output_label.setBuddy(self.output)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(160 if paste else 16777215)
        self.summary.setMinimumHeight(100 if paste else 180)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.analyze_button, self.generate_button = QPushButton(), QPushButton()
        self.cancel_button, self.copy_button, self.clear_button = QPushButton(), QPushButton(), QPushButton()
        self.analyze_button.clicked.connect(self.start_analysis)
        self.generate_button.clicked.connect(self.restore_text if paste else self._choose_output)
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.copy_button.clicked.connect(self.copy_result)
        self.clear_button.clicked.connect(self.clear_selection)
        set_button_role(self.generate_button, "primary")
        top = QHBoxLayout()
        top.addWidget(self.select_button)
        if paste:
            top.addWidget(self.mode_combo)
        top.addWidget(self.clear_button)
        top.addStretch()
        actions = QHBoxLayout()
        for widget in (self.analyze_button, self.generate_button, self.cancel_button, self.copy_button):
            actions.addWidget(widget)
        actions.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.path_label)
        layout.addWidget(self.help_label)
        if paste:
            columns = QHBoxLayout()
            for label, editor in ((self.input_label, self.input), (self.output_label, self.output)):
                column = QVBoxLayout()
                column.addWidget(label)
                column.addWidget(editor)
                columns.addLayout(column)
            layout.addLayout(columns, 1)
        layout.addLayout(actions)
        layout.addWidget(self.summary, 1 if not paste else 0)
        layout.addWidget(self.status_label)
        self.path_label.setVisible(not paste)
        self.copy_button.setVisible(paste)
        self.retranslate(self._translator)
        self._update_actions()

    def retranslate(self, translator):
        self._translator = translator
        for widget, key in (
            (self.select_button, "narrative.paste" if self.paste else "restoration.file.select"),
            (self.help_label, "narrative.help_text" if self.paste else "narrative.help_file"),
            (self.analyze_button, "restoration.csv.analyze"),
            (self.generate_button, "narrative.restore_text" if self.paste else "narrative.restore_file"),
            (self.cancel_button, "dialog.button.cancel"), (self.copy_button, "narrative.copy"),
            (self.clear_button, "narrative.clear"), (self.input_label, "narrative.input"),
            (self.output_label, "narrative.output"),
        ):
            widget.setText(translator.text(key))
        for index in range(self.mode_combo.count()):
            self.mode_combo.setItemText(index, translator.text("narrative.mode." + self.mode_combo.itemData(index)))
        self.input.setAccessibleName(translator.text("narrative.input"))
        self.output.setAccessibleName(translator.text("narrative.output"))
        self.mode_combo.setAccessibleName(translator.text("narrative.mode.label"))
        self.summary.setAccessibleName(translator.text("narrative.analysis"))
        if self._result is not None:
            self.summary.setPlainText(result_text(self._result, translator))
        self.status_label.setText(translator.text(self._status_id))

    def invalidate(self, *_args):
        self._revision += 1
        self.output.clear()
        self._result = None
        self.summary.clear()
        self._set_status("narrative.initial_text" if self.paste else "narrative.initial_file")
        self._update_actions()

    def load_document(self, path):
        if self._worker is not None:
            return
        from data_mask_studio.narrative_restoration import FORMATS
        if Path(path).suffix.casefold() not in FORMATS:
            return
        self._inspection = Path(path).absolute()
        self._last_output_path = None
        self.path_label.setText(str(self._inspection))
        self.invalidate()
        self.file_loaded.emit()

    def start_analysis(self):
        if self.paste:
            text, mode = self.input.toPlainText(), self.mode_combo.currentData()
            self._start(lambda cancel: self.service.restore_text(text, mode=mode, analyze=True, should_cancel=cancel), "analyze")
        elif self._inspection:
            path = self._inspection
            self._start(lambda cancel: self.service.analyze_file(path, should_cancel=cancel), "analyze")

    def restore_text(self):
        text, mode = self.input.toPlainText(), self.mode_combo.currentData()
        self._start(lambda cancel: self.service.restore_text(text, mode=mode, should_cancel=cancel), "restore")

    def _choose_output(self):
        if self._inspection is None or self._worker is not None:
            return
        suggested = self._inspection.with_name(self._inspection.stem + "_restored" + self._inspection.suffix)
        path, _ = QFileDialog.getSaveFileName(self, self._translator.text("narrative.restore_file"), str(suggested),
                                              "*" + self._inspection.suffix,
                                              options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.restore_to(path)

    def restore_to(self, destination):
        path = self._inspection
        if path:
            self._start(lambda cancel: self.service.restore_file(path, destination, should_cancel=cancel), "restore")

    def _start(self, operation, kind):
        if self._worker is not None:
            return
        self.output.clear()
        self._result = None
        self._operation, self._operation_revision = kind, self._revision
        self._set_status("narrative.working")
        worker = NarrativeWorker(operation)
        self._worker = worker
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.cancelled.connect(lambda: self._set_status("narrative.error.cancelled"))
        worker.finished.connect(self._finished)
        self._update_actions()
        self.busy_changed.emit(True)
        worker.start()

    def _completed(self, result):
        if self._operation_revision != self._revision:
            return
        self._result = result
        self.summary.setPlainText(result_text(result, self._translator))
        if self._operation == "restore" and result.text is not None:
            self.output.setPlainText(result.text)
        self._last_output_path = result.output_path
        self._set_status("narrative.review" if result.skipped_occurrences or result.invalid_occurrences else "narrative.done")

    def _failed(self, error):
        if self._operation_revision != self._revision:
            return
        self.output.clear()
        if isinstance(error, NarrativeError):
            self._set_status(error.message_id, True)
            self.summary.setPlainText("\n".join(error.codes))
        else:
            self._set_status("narrative.error.vault", True)

    def _finished(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self._update_actions()
        self.busy_changed.emit(False)

    def _update_actions(self):
        busy = self._worker is not None
        ready = bool(self.input.toPlainText()) if self.paste else self._inspection is not None
        for widget in (self.select_button, self.clear_button, self.mode_combo, self.analyze_button, self.generate_button):
            widget.setEnabled(not busy)
        self.analyze_button.setEnabled(ready and not busy)
        self.generate_button.setEnabled(ready and not busy)
        self.copy_button.setEnabled(bool(self.output.toPlainText()) and not busy)
        self.cancel_button.setVisible(busy)

    def _set_status(self, key, error=False):
        self._status_id = key
        self.status_label.setText(self._translator.text(key))
        set_feedback_state(self.status_label, "error" if error else "neutral")

    def paste_clipboard(self):
        if self._worker is None:
            self.input.setPlainText(QApplication.clipboard().text())

    def copy_result(self):
        if self.copy_button.isEnabled() and self.output.toPlainText():
            QApplication.clipboard().setText(self.output.toPlainText())

    def clear_selection(self):
        if self._worker is not None:
            return
        self._inspection = None
        self._last_output_path = None
        self.path_label.clear()
        self.input.clear()
        self.invalidate()

    def cancel_processing(self):
        if self._worker is not None:
            self._worker.request_cancel()

    def has_running_worker(self):
        return self._worker is not None

    def stop_worker(self):
        if self._worker is not None:
            self._worker.request_cancel()
            return self._worker.wait(5000)
        return True
