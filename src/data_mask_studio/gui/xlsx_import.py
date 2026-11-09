"""One import dialog shared by masking and restoration, single and batch."""

from pathlib import Path

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QFileDialog, QProgressDialog

from data_mask_studio.xlsx_import import WorkbookImportError, convert_workbooks, is_workbook_input, validate_workbook


class _ImportWorker(QThread):
    def __init__(self, sources, destination, parent):
        super().__init__(parent)
        self.sources, self.destination = sources, destination
        self.paths = []
        self.failure = None

    def run(self):
        try:
            self.paths = convert_workbooks(self.sources, self.destination, should_cancel=self.isInterruptionRequested)
        except WorkbookImportError as error:
            self.failure = error
        except Exception:
            self.failure = WorkbookImportError("xlsx.error.format")


def prepare_tabular_paths(paths, parent, translator):
    paths = list(dict.fromkeys(Path(p) for p in paths))
    workbooks = [path for path in paths if is_workbook_input(path)]
    if not workbooks:
        return paths
    for path in workbooks:
        validate_workbook(path)
    destination = QFileDialog.getExistingDirectory(parent, translator.text("xlsx.destination"))
    if not destination:
        return []
    dialog = QProgressDialog(translator.text("xlsx.converting"), translator.text("dialog.button.cancel"), 0, 0, parent)
    dialog.setWindowTitle(translator.text("xlsx.title"))
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setMinimumDuration(0)
    worker = _ImportWorker(workbooks, destination, parent)
    dialog.canceled.connect(worker.requestInterruption)
    worker.finished.connect(dialog.accept)
    worker.start()
    dialog.exec()
    if worker.isRunning():
        worker.requestInterruption()
    worker.wait()
    dialog.deleteLater()
    worker.deleteLater()
    if worker.failure is not None:
        if worker.failure.message_id == "xlsx.error.cancelled":
            return []
        raise worker.failure
    return [path for path in paths if path not in workbooks] + worker.paths
