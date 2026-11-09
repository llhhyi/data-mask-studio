"""Compose the existing CSV and HTML workflows; do not interpret their data."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from data_mask_studio.gui.components.presentation import set_button_role, set_feedback_state
from data_mask_studio.gui.html_restoration_widget import HTMLRestorationWidget
from data_mask_studio.gui.restoration_widget import RestorationWidget
from data_mask_studio.gui.narrative_restoration_widget import NarrativeRestorationWidget
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.i18n import Translator
from data_mask_studio.gui.xlsx_import import prepare_tabular_paths
from data_mask_studio.xlsx_import import WorkbookImportError
from data_mask_studio.vault import VaultRepository


class _WorkflowStack(QStackedWidget):
    # Hidden workflows must not impose their minimum height on the idle page
    # or on the other format. PageShell remains responsible for outer scrolling.
    def sizeHint(self):
        widget = self.currentWidget()
        return widget.sizeHint() if widget is not None else super().sizeHint()

    def minimumSizeHint(self):
        widget = self.currentWidget()
        return widget.minimumSizeHint() if widget is not None else super().minimumSizeHint()


class FileRestorationWidget(QWidget):
    workbooks_imported = Signal(object)
    def __init__(self, repository_factory: Callable[[], VaultRepository], *, translator: Translator | None = None) -> None:
        super().__init__()
        self._translator = translator or Translator("pt-BR")
        self._selection_message_id: str | None = None
        self.select_button = QPushButton("Selecionar arquivo mascarado")
        set_button_role(self.select_button, "primary")
        self.select_button.clicked.connect(self._select_file)
        self.guidance = QLabel()
        self.guidance.setWordWrap(True)
        set_feedback_state(self.guidance, "neutral")
        idle = QWidget()
        idle_layout = QVBoxLayout(idle)
        idle_layout.setContentsMargins(0, 0, 0, 0)
        action = QHBoxLayout()
        action.addWidget(self.select_button)
        action.addStretch()
        idle_layout.addLayout(action)
        idle_layout.addWidget(self.guidance)
        idle_layout.addStretch()
        self.csv_widget = RestorationWidget(repository_factory, file_selector=self._select_file,
                                            translator=self._translator)
        self.html_widget = HTMLRestorationWidget(repository_factory, file_selector=self._select_file,
                                                translator=self._translator)
        self.document_widget = NarrativeRestorationWidget(repository_factory, file_selector=self._select_file,
                                                          translator=self._translator)
        self.stack = _WorkflowStack()
        for widget in (idle, self.csv_widget, self.html_widget, self.document_widget):
            widget.layout().setContentsMargins(0, 0, 0, 0)
            self.stack.addWidget(widget)
        self.format_label = QLabel("Formatos compatíveis: CSV, HTML e HTM.")
        self.format_label.setWordWrap(True)
        self.format_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        set_feedback_state(self.format_label, "neutral")
        self.selection_status = QLabel()
        self.selection_status.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_status.setWordWrap(True)
        self.selection_status.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addWidget(self.format_label)
        layout.addWidget(self.selection_status)
        layout.addWidget(self.stack, stretch=1)
        self.csv_widget.file_loaded.connect(lambda: self._file_loaded(self.csv_widget))
        self.html_widget.file_loaded.connect(lambda: self._file_loaded(self.html_widget))
        self.document_widget.file_loaded.connect(lambda: self._file_loaded(self.document_widget))
        self.csv_widget.busy_changed.connect(self._sync_busy)
        self.html_widget.busy_changed.connect(self._sync_busy)
        self.document_widget.busy_changed.connect(self._sync_busy)
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        self._translator = translator
        self.select_button.setText(translator.text("restoration.file.select"))
        self.guidance.setText(translator.text("restoration.file.guidance"))
        self.csv_widget.retranslate(translator)
        self.html_widget.retranslate(translator)
        self.document_widget.retranslate(translator)
        self._render_format()
        if self._selection_message_id is not None:
            self.selection_status.setText(translator.text(self._selection_message_id))

    def _render_format(self) -> None:
        current = self.stack.currentWidget()
        message_id = ("restoration.file.csv_type" if current is self.csv_widget else
                      "restoration.file.html_type" if current is self.html_widget else
                      "narrative.file_type" if current is self.document_widget else
                      "restoration.file.formats")
        self.format_label.setText(self._translator.text(message_id))

    def _sync_busy(self, *_args: object) -> None:
        self.select_button.setEnabled(not self._busy())

    def _busy(self) -> bool:
        # A finished thread can still have queued terminal callbacks. Wait for
        # the existing widget to retire its reference, not merely isRunning().
        return any(widget._worker is not None for widget in (self.csv_widget, self.html_widget, self.document_widget))

    def _select_file(self) -> None:
        if self._busy():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, self._translator.text("restoration.file.select"), "",
            self._translator.text("restoration.file.filter"),
        )
        if path:
            self.load_file(path)

    def load_file(self, path: str | Path) -> bool:
        if self._busy():
            return False
        extension = Path(path).suffix.casefold()
        if extension in {".md", ".markdown", ".docx", ".json"}:
            self.document_widget.load_document(path)
            return True
        if extension not in {".csv", ".xlsx", ".html", ".htm"}:
            self._selection_failed(self._translator.text("restoration.file.unsupported"))
            self._selection_message_id = "restoration.file.unsupported"
            return False
        if extension in {".csv", ".xlsx"}:
            try:
                paths = prepare_tabular_paths([path], self, self._translator)
            except WorkbookImportError as error:
                self._selection_failed(self._translator.text(error.message_id))
                return False
            if not paths:
                return False
            if len(paths) > 1:
                self.workbooks_imported.emit(paths)
                return True
            path = paths[0]
        widget = self.csv_widget if extension in {".csv", ".xlsx"} else self.html_widget
        previous = widget._inspection
        # The existing inspectors validate readability, encoding and structure;
        # an extension is only the dispatch hint, never proof of a valid input.
        if widget is self.csv_widget:
            widget.load_csv(str(path))
        else:
            widget.load_html(str(path))
        if widget._inspection is previous:
            self._selection_failed(widget.status_label.text())
            # Preserve the HTML inspector's presentation identity, not its text.
            # Current selection errors have no interpolation parameters.
            if widget is self.html_widget and widget._status_message is not None:
                message_id, parameters = widget._status_message
                if not parameters:
                    self._selection_message_id = message_id
            return False
        return True

    def _file_loaded(self, widget: QWidget) -> None:
        for other in (self.csv_widget, self.html_widget, self.document_widget):
            if other is not widget:
                other.clear_selection()
        self._selection_message_id = None
        self.selection_status.clear()
        self.selection_status.hide()
        self.stack.setCurrentWidget(widget)
        self._render_format()
        self.stack.updateGeometry()
        self.updateGeometry()

    def _selection_failed(self, message: str) -> None:
        self._selection_message_id = None
        self.selection_status.setText(message)
        set_feedback_state(self.selection_status, "error")
        self.selection_status.show()

    def clear_selection(self) -> None:
        if self._busy():
            return
        self.csv_widget.clear_selection()
        self.html_widget.clear_selection()
        self.document_widget.clear_selection()
        self.stack.setCurrentIndex(0)
        self._render_format()
        self._selection_message_id = None
        self.selection_status.clear()
        self.selection_status.hide()
        self.stack.updateGeometry()

    def stop_worker(self) -> bool:
        return all(widget.stop_worker() for widget in (self.csv_widget, self.html_widget, self.document_widget))
