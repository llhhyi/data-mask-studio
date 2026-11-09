"""Local, values-only XLSX import. All nonempty sheets become UTF-8 CSVs."""

import csv
import hashlib
from datetime import date, datetime, time, timedelta
import os
from pathlib import Path
import re
import tempfile
import zipfile
from xml.etree.ElementTree import ParseError
from collections.abc import Callable, Iterable

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from defusedxml.common import DefusedXmlException


class WorkbookImportError(ValueError):
    def __init__(self, message_id: str):
        self.message_id = message_id
        super().__init__(message_id)


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_CELLS = 2_000_000


def is_workbook_input(path: str | Path) -> bool:
    source = Path(path)
    if source.suffix.casefold() == ".xlsx":
        return True
    # Also recognize Excel files that were only renamed to .csv.
    try:
        with source.open("rb") as stream:
            if stream.read(4) != b"PK\x03\x04":
                return False
        with zipfile.ZipFile(source) as archive:
            return "xl/workbook.xml" in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _fingerprint(source: Path) -> str:
    with source.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_archive(source: Path) -> str:
    stat = source.stat()
    if stat.st_size > MAX_INPUT_BYTES:
        raise WorkbookImportError("xlsx.error.limit")
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
            raise WorkbookImportError("xlsx.error.format")
        if len(entries) > 10_000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise WorkbookImportError("xlsx.error.limit")
        if any(name.casefold().endswith("vbaproject.bin") for name in names):
            raise WorkbookImportError("xlsx.error.macros")
    return _fingerprint(source)


def validate_workbook(source: str | Path) -> None:
    """Reject invalid inputs before asking the user to choose an output folder."""
    try:
        _check_archive(Path(source))
    except WorkbookImportError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile):
        raise WorkbookImportError("xlsx.error.format") from None


def _filename(stem: str, sheet: str) -> str:
    def clean(value):
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:120] or "sheet"
    return f"{clean(stem)}__{clean(sheet)}.csv"


def _value(cell) -> str:
    value = cell.value
    if value is None:
        return ""
    if cell.data_type == "e":
        raise WorkbookImportError("xlsx.error.cell_error")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ") if value.time() != time() else value.date().isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    # Preserve explicitly zero-padded identifier formats without rounding amounts.
    if isinstance(value, int) and not isinstance(value, bool) and re.fullmatch(r"0{2,}", cell.number_format or ""):
        return str(value).zfill(len(cell.number_format))
    return str(value)


def convert_workbooks(
    sources: Iterable[str | Path], destination: str | Path, *,
    should_cancel: Callable[[], bool] = lambda: False,
) -> list[Path]:
    """Stage all files, then publish without overwriting any existing destination.

    Source workbooks stay untouched. The chosen directory contains sensitive
    plain-text values; no automatic upload, formula evaluation or key access.
    """
    source_paths = list(dict.fromkeys(Path(p).resolve() for p in sources))
    folder = Path(destination).resolve()
    if not folder.is_dir():
        raise WorkbookImportError("xlsx.error.destination")
    staged_outputs = []
    published = []
    fingerprints = {}
    names = set()
    try:
        with tempfile.TemporaryDirectory(prefix=".dms-xlsx-", dir=folder) as temporary:
            try:
                for source in source_paths:
                    if should_cancel():
                        raise WorkbookImportError("xlsx.error.cancelled")
                    fingerprints[source] = _check_archive(source)
                    # File objects allow content detection even for misnamed .csv files.
                    with source.open("rb") as formula_stream, source.open("rb") as value_stream:
                        formula_book = load_workbook(formula_stream, read_only=True, data_only=False, keep_links=False)
                        try:
                            value_book = load_workbook(value_stream, read_only=True, data_only=True, keep_links=False)
                            try:
                                for sheet in formula_book.worksheets:
                                    if (sheet.max_row or 1) * (sheet.max_column or 1) > MAX_CELLS:
                                        raise WorkbookImportError("xlsx.error.limit")
                                    cached_sheet = value_book[sheet.title]
                                    # Some producers under-report dimensions; never silently
                                    # discard cells just because the dimension says A1:A1.
                                    sheet.reset_dimensions()
                                    cached_sheet.reset_dimensions()
                                    width = row_count = 0
                                    for row in sheet.iter_rows():
                                        if should_cancel():
                                            raise WorkbookImportError("xlsx.error.cancelled")
                                        width = max(width, len(row))
                                        row_count += 1
                                        if width * row_count > MAX_CELLS:
                                            raise WorkbookImportError("xlsx.error.limit")
                                    if not width:
                                        continue
                                    name = _filename(source.stem, sheet.title)
                                    target = folder / name
                                    if name.casefold() in names or target.exists() or target.is_symlink():
                                        raise WorkbookImportError("xlsx.error.exists")
                                    names.add(name.casefold())
                                    staged = Path(temporary) / name
                                    nonempty = False
                                    cell_count = 0
                                    with staged.open("w", encoding="utf-8-sig", newline="") as output:
                                        writer = csv.writer(output)
                                        for formulas, values in zip(
                                            sheet.iter_rows(max_col=width), cached_sheet.iter_rows(max_col=width), strict=True,
                                        ):
                                            if should_cancel():
                                                raise WorkbookImportError("xlsx.error.cancelled")
                                            cell_count += len(formulas)
                                            if cell_count > MAX_CELLS:
                                                raise WorkbookImportError("xlsx.error.limit")
                                            for formula, cached in zip(formulas, values, strict=True):
                                                if formula.data_type == "f" and cached.value is None:
                                                    raise WorkbookImportError("xlsx.error.formula_cache")
                                            row = [_value(cell) for cell in values]
                                            nonempty = nonempty or any(value != "" for value in row)
                                            writer.writerow(row)
                                        output.flush()
                                        os.fsync(output.fileno())
                                    if nonempty:
                                        staged_outputs.append((staged, target))
                            finally:
                                value_book.close()
                        finally:
                            formula_book.close()
                if not staged_outputs:
                    raise WorkbookImportError("xlsx.error.empty")
                for source, fingerprint in fingerprints.items():
                    if fingerprint != _fingerprint(source):
                        raise WorkbookImportError("xlsx.error.changed")
                if should_cancel():
                    raise WorkbookImportError("xlsx.error.cancelled")
                for staged, target in staged_outputs:
                    os.link(staged, target)
                    published.append((staged, target))
                return [target for _, target in staged_outputs]
            except BaseException:
                # Only remove our own newly published inode, never a replacement.
                for staged, target in reversed(published):
                    if target.exists() and os.path.samefile(staged, target):
                        target.unlink()
                raise
    except WorkbookImportError:
        raise
    except FileExistsError:
        raise WorkbookImportError("xlsx.error.exists") from None
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, EOFError,
            ParseError, InvalidFileException, DefusedXmlException):
        raise WorkbookImportError("xlsx.error.format") from None
