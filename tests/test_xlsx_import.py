import csv
from datetime import date
import hashlib
from pathlib import Path
from unittest.mock import patch
import zipfile

import pytest
from openpyxl import Workbook

from data_mask_studio.xlsx_import import convert_workbooks, is_workbook_input, WorkbookImportError
from data_mask_studio.csv_tools import inspect_csv, CSVInspectionError


def fixture_book(path, *, formula=False, extra_sheet=True):
    book = Workbook()
    sheet = book.active
    sheet.title = "营业日报"
    sheet.append(["日期", "供应商", "人员", "账号", "金额", "备注"])
    sheet.append([date(2026, 11, 4), "合成供应商甲", "合成张三", 123, 1234.5, "备注,引号\"\n换行"])
    sheet["D2"].number_format = "000000"
    if formula:
        sheet["E2"] = "=1+2"
    if extra_sheet:
        other = book.create_sheet("另一店")
        other.append(["供应商", "人员"])
        other.append(["合成供应商甲", "合成张三"])
    book.create_sheet("空表")
    book.save(path)
    return path


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream))


def test_all_nonempty_sheets_to_csv_without_touching_source(tmp_path):
    source = fixture_book(tmp_path / "报表.xlsx")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = convert_workbooks([source], tmp_path)
    assert [p.name for p in result] == ["报表__营业日报.csv", "报表__另一店.csv"]
    assert rows(result[0])[1] == ["2026-11-04", "合成供应商甲", "合成张三", "000123", "1234.5", "备注,引号\"\n换行"]
    assert result[0].read_bytes().startswith(b"\xef\xbb\xbf")
    assert inspect_csv(result[0]).headers[1] == "供应商"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob(".dms-xlsx-*"))


def test_misnamed_csv_is_detected_by_content(tmp_path):
    source = fixture_book(tmp_path / "报表.csv", extra_sheet=False)
    assert is_workbook_input(source)
    with pytest.raises(CSVInspectionError) as captured:
        inspect_csv(source)
    assert captured.value.message_id == "xlsx.error.excel_as_csv"
    assert len(convert_workbooks([source], tmp_path)) == 1


def test_existing_output_is_never_overwritten(tmp_path):
    source = fixture_book(tmp_path / "报表.xlsx")
    existing = tmp_path / "报表__另一店.csv"
    existing.write_text("must survive", encoding="utf-8")
    with pytest.raises(WorkbookImportError, match="xlsx.error.exists"):
        convert_workbooks([source], tmp_path)
    assert existing.read_text(encoding="utf-8") == "must survive"
    assert not (tmp_path / "报表__营业日报.csv").exists()


def test_formula_without_cached_result_does_not_become_blank(tmp_path):
    source = fixture_book(tmp_path / "公式.xlsx", formula=True)
    with pytest.raises(WorkbookImportError, match="xlsx.error.formula_cache"):
        convert_workbooks([source], tmp_path)
    assert not list(tmp_path.glob("*.csv"))


def test_macros_are_rejected(tmp_path):
    source = fixture_book(tmp_path / "含宏.xlsx")
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("xl/vbaProject.bin", b"synthetic")
    with pytest.raises(WorkbookImportError, match="xlsx.error.macros"):
        convert_workbooks([source], tmp_path)


def test_cell_limit_and_cancellation_leave_no_output(tmp_path):
    source = fixture_book(tmp_path / "报表.xlsx")
    with patch("data_mask_studio.xlsx_import.MAX_CELLS", 1):
        with pytest.raises(WorkbookImportError, match="xlsx.error.limit"):
            convert_workbooks([source], tmp_path)
    with pytest.raises(WorkbookImportError, match="xlsx.error.cancelled"):
        convert_workbooks([source], tmp_path, should_cancel=lambda: True)
    assert not list(tmp_path.glob("*.csv"))


def test_partial_publication_is_rolled_back(tmp_path, monkeypatch):
    source = fixture_book(tmp_path / "报表.xlsx")
    import data_mask_studio.xlsx_import as module
    original = module.os.link
    calls = []

    def fail_second(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return original(source, destination)

    monkeypatch.setattr(module.os, "link", fail_second)
    with pytest.raises(WorkbookImportError):
        convert_workbooks([source], tmp_path)
    assert not list(tmp_path.glob("*.csv"))


def test_csv_passthrough_has_no_excel_detection(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("名称,金额\n合成数据,12\n", encoding="utf-8-sig")
    assert not is_workbook_input(source)


def test_empty_workbook_is_rejected(tmp_path):
    source = tmp_path / "空.xlsx"
    Workbook().save(source)
    with pytest.raises(WorkbookImportError, match="xlsx.error.empty"):
        convert_workbooks([source], tmp_path)


def test_trailing_blank_cells_are_padded_and_bad_dimensions_do_not_drop_data(tmp_path):
    original = fixture_book(tmp_path / "original.xlsx", extra_sheet=False)
    source = tmp_path / "bad-dimensions.xlsx"
    with zipfile.ZipFile(original) as input_zip, zipfile.ZipFile(source, "w") as output_zip:
        for item in input_zip.infolist():
            content = input_zip.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b'ref="A1:F2"', b'ref="A1:A1"')
            output_zip.writestr(item, content)
    destination = tmp_path / "out"
    destination.mkdir()
    output = convert_workbooks([source], destination)[0]
    assert len(rows(output)[0]) == len(rows(output)[1]) == 6
    assert rows(output)[1][-1] == "备注,引号\"\n换行"


def test_sparse_rows_keep_rectangular_csv(tmp_path):
    source = tmp_path / "sparse.xlsx"
    book = Workbook()
    book.active.append(["名称", "金额", "备注"])
    book.active.append(["合成名称", 12])
    book.save(source)
    output = convert_workbooks([source], tmp_path)[0]
    assert rows(output) == [["名称", "金额", "备注"], ["合成名称", "12", ""]]


def test_excel_batch_masking_and_returned_excel_restoration(tmp_path):
    from data_mask_studio.anonymization import ColumnConfig
    from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService
    from data_mask_studio.profiles import ProfileRepository, ProfileService
    from data_mask_studio.restoration import restore_csv
    from data_mask_studio.vault import VaultRepository, VaultCipher
    from test_chinese_workflow import FixedKeyProvider, restore_configuration

    source = tmp_path / "多月.xlsx"
    book = Workbook()
    for index in range(2):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.title = f"月份{index + 1}"
        sheet.append(["供应商", "人员", "金额"])
        sheet.append(["合成供应商甲", "合成张三", 10 + index])
    book.save(source)
    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    converted = convert_workbooks([source], intermediate)
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("同类报表", [ColumnConfig("供应商", True, "SUPPLIER"),
                                         ColumnConfig("人员", True, "PERSON"), ColumnConfig("金额")])
    vault = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    batch = BatchService(service)
    files = [BatchFile(p) for p in converted]
    batch.validate(files, profile)
    masked = tmp_path / "masked"
    masked.mkdir()
    batch.process(files, profile, masked, FixedKeyProvider(), lambda: vault)
    assert all(item.status is BatchFileStatus.COMPLETED for item in files)
    first, second = [rows(item.output_path)[1] for item in files]
    assert first[:2] == second[:2]
    returned = tmp_path / "对方返回.xlsx"
    book = Workbook()
    book.active.append(["供方代号", "经办人代号", "汇总"])
    book.active.append([first[0], second[1], 21])
    book.save(returned)
    return_csv = convert_workbooks([returned], intermediate)[0]
    restored = tmp_path / "restored.csv"
    result = restore_csv(restore_configuration(return_csv), restored, vault)
    assert result.restored_codes == 2
    assert rows(restored)[1] == ["合成供应商甲", "合成张三", "21"]
