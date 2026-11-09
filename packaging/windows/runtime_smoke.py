"""Opt-in build probe: verify the real window and features, then exit without dialogs."""

import os


def _build_probe():
    import json
    import ctypes
    from pathlib import Path

    report = Path(os.environ["DMS_BUILD_SMOKE_REPORT"])
    root = Path(os.environ["LOCALAPPDATA"])
    ctypes.WinDLL("kernel32").SetErrorMode(0x0001 | 0x8000)
    if report.name != "packaging-smoke.json" or report.parent.resolve() != root.resolve():
        os._exit(2)

    def write(value):
        temporary = report.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")
        temporary.replace(report)

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    def fail(error):
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        modules = {}
        for name in ("icuuc.dll", "icu.dll", "ucrtbase.dll", "msvcp140.dll", "vcruntime140.dll", "Qt6Core.dll", "Qt6Gui.dll", "pyside6.abi3.dll", "shiboken6.abi3.dll"):
            handle = kernel.GetModuleHandleW(name)
            if handle:
                buffer = ctypes.create_unicode_buffer(32768)
                kernel.GetModuleFileNameW(handle, buffer, len(buffer))
                modules[name] = buffer.value
        write({"status": "error", "error": str(error), "loaded_libraries": modules})
        os._exit(1)

    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtCore import QTimer, qVersion
        from PySide6.QtWidgets import QApplication, QMainWindow
    except Exception as error:
        fail(error)

    original_exec = QApplication.exec

    def verify(application):
        try:
            from io import BytesIO
            from zipfile import ZipFile
            from importlib.metadata import version
            from data_mask_studio.anonymization import generate_token
            from data_mask_studio.i18n import load_catalogs
            from data_mask_studio.vault import VaultRepository, VaultCipher, MappingCandidate
            from data_mask_studio.narrative_restoration import NarrativeRestorationService
            from data_mask_studio.xlsx_import import convert_workbooks
            from openpyxl import Workbook

            windows = [widget for widget in application.topLevelWidgets() if isinstance(widget, QMainWindow)]
            require(len(windows) == 1 and windows[0].page_stack.count() == 9, "Main window not initialized")
            require(windows[0]._translator.locale == "zh-CN", "Wrong first-start language")
            require(not application.windowIcon().isNull(), "Missing icon")
            require(version("data-mask-studio") == "1.0.0", "Wrong application version")
            catalogs = load_catalogs()
            require(len(catalogs) == 3 and len(catalogs["zh-CN"]) == len(catalogs["en"]), "Incomplete catalogs")

            case = root / "synthetic-only"
            case.mkdir()
            value = "合成构建验证"
            code = generate_token(b"H" * 32, "TEST", value)
            vault = VaultRepository(case / "vault.db", VaultCipher(b"V" * 32))
            with vault.transaction() as transaction:
                transaction.upsert_batch([MappingCandidate(code, "TEST", value, "名称")])
            service = NarrativeRestorationService(lambda: vault)
            require(service.restore_text(code, mode="plain").text == value, "Plain-text restoration failed")
            require(json.loads(service.restore_text(json.dumps({"name": code}), mode="json").text) == {"name": value}, "JSON restoration failed")
            markdown = service.restore_text(f"{code}\n\n`{code}`", mode="markdown")
            require(markdown.restored_occurrences == 1 and markdown.skipped_occurrences == 1, "Markdown restoration failed")

            stream = BytesIO()
            with ZipFile(stream, "w") as archive:
                archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
                archive.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{code}</w:t></w:r></w:p></w:body></w:document>')
            word = case / "source.docx"
            word.write_bytes(stream.getvalue())
            require(service.restore_file(word, case / "restored.docx").restored_occurrences == 1, "Word restoration failed")

            workbook = Workbook()
            workbook.active.append(["名称", "金额"])
            workbook.active.append([value, "0010.50"])
            workbook.save(case / "source.xlsx")
            (case / "csv").mkdir()
            converted = convert_workbooks([case / "source.xlsx"], case / "csv")
            require(len(converted) == 1 and converted[0].is_file(), "XLSX import failed")
            write({"status": "passed", "application_version": version("data-mask-studio"), "qt_version": qVersion(),
                   "pages": 9, "locale": "zh-CN", "catalog_messages": len(catalogs["zh-CN"]),
                   "checks": ["real-main-window", "icon", "local-encrypted-vault", "plain-text", "markdown", "json", "word", "xlsx-import"]})
            application.quit()
        except Exception as error:
            fail(error)

    def exec_and_verify(application):
        QTimer.singleShot(0, lambda: verify(application))
        try:
            return original_exec()
        except Exception as error:
            fail(error)

    QApplication.exec = exec_and_verify


if os.environ.get("DMS_BUILD_SMOKE_REPORT"):
    _build_probe()
