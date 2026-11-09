"""Chinese report hand-off: real batch service and a shared encrypted vault."""

import csv
from decimal import Decimal
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService
from data_mask_studio.csv_tools import anonymize_csv, inspect_csv
from data_mask_studio.i18n import Translator, load_catalogs
from data_mask_studio.i18n.catalog import placeholder_names
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.preferences import PreferencesService, resolve_language
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.restoration import (
    MissingCodePolicy, RestorationConfiguration, SelectedColumn, restore_csv,
)
from data_mask_studio.restoration.exceptions import MissingCodeError
from data_mask_studio.security import LocalKeyProvider
from data_mask_studio.vault import VaultCipher, VaultRepository


class FixedKeyProvider:
    def get_key(self):
        return b"S" * 32


def write_rows(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows(rows)


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream))


def restore_configuration(path, selected=(0, 1)):
    inspection = inspect_csv(path)
    return RestorationConfiguration(
        source_path=path, encoding=inspection.encoding, delimiter=inspection.delimiter,
        headers=tuple(inspection.headers),
        selected_columns=tuple(SelectedColumn(i, inspection.headers[i]) for i in selected),
        missing_code_policy=MissingCodePolicy.ABORT,
    )


def test_chinese_catalog_and_saved_system_language(tmp_path):
    catalogs = load_catalogs()
    assert set(catalogs["zh-CN"]) == set(catalogs["en"])
    for identifier, template in catalogs["zh-CN"].items():
        assert placeholder_names(template) == placeholder_names(catalogs["en"][identifier])
    assert Translator("zh-CN").text("navigation.batch_masking") == "批量脱敏"
    service = PreferencesService(tmp_path / "prefs.json", ui_languages_provider=lambda: ("zh-CN", "en-US"))
    assert service.resolve_language(service.load().preferences.language) == "zh-CN"
    assert service.save_language("zh-CN").error is None
    assert service.load().preferences.language.value == "zh-CN"
    assert resolve_language("system", ("zh-Hans-CN",)) == "zh-CN"
    assert resolve_language("system", ("en-US", "zh-CN")) == "en"


def test_two_file_batch_same_tokens_and_reordered_aggregated_report_restores(tmp_path):
    headers = ["供应商名称", "员工姓名", "银行账号", "金额", "备注"]
    supplier = "合成供应商甲有限公司"
    person = "合成人员张三"
    account = "00123456789012345678"
    first = tmp_path / "一月.csv"
    second = tmp_path / "二月.csv"
    write_rows(first, [headers, [supplier, person, account, "1234.50", "合成,备注\n第二行"]])
    write_rows(second, [list(reversed(headers)), ["合成备注", "20.25", account, person, supplier]])
    originals = {p: p.read_bytes() for p in (first, second)}
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("供应商与人员脱敏", [
        ColumnConfig(headers[0], True, "SUPPLIER", NormalizationRule.EXACT),
        ColumnConfig(headers[1], True, "PERSON", NormalizationRule.EXACT),
        ColumnConfig(headers[2], True, "ACCOUNT", NormalizationRule.EXACT),
        ColumnConfig(headers[3]), ColumnConfig(headers[4]),
    ])
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    output = tmp_path / "脱敏文件"
    output.mkdir()
    files = [BatchFile(first), BatchFile(second)]
    batch = BatchService(service)
    batch.validate(files, profile)
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in files)
    batch.process(files, profile, output, FixedKeyProvider(), lambda: repository)
    assert all(item.status is BatchFileStatus.COMPLETED for item in files)
    tables = [read_rows(item.output_path) for item in files]
    records = [dict(zip(table[0], table[1])) for table in tables]
    for header in headers[:3]:
        assert records[0][header] == records[1][header]
        assert records[0][header] not in {supplier, person, account}
    assert records[0]["备注"] == "合成,备注\n第二行"
    assert records[0]["金额"] == "1234.50"
    assert records[1]["金额"] == "20.25"
    # A different source schema still shares codes with explicit matching prefixes.
    third = tmp_path / "其他列名.csv"
    write_rows(third, [["供方", "经办人"], [supplier, person]])
    third_output = tmp_path / "其他列名-输出.csv"
    anonymize_csv(third, third_output, encoding="utf-8-sig", delimiter=",",
                  configurations=[ColumnConfig("供方", True, "SUPPLIER"), ColumnConfig("经办人", True, "PERSON")],
                  secret_key=FixedKeyProvider().get_key(), vault_repository=repository)
    assert read_rows(third_output)[1] == [records[0][h] for h in headers[:2]]
    returned = tmp_path / "对方返回汇总.csv"
    total = sum(Decimal(record["金额"]) for record in records)
    write_rows(returned, [["供方代号", "经办人代号", "金额合计"],
                          [records[0][headers[0]], records[0][headers[1]], str(total)]])
    restored = tmp_path / "汇总还原.csv"
    result = restore_csv(restore_configuration(returned), restored, repository.as_read_only())
    assert result.restored_codes == 2 and result.missing_codes == 0
    assert read_rows(restored)[1] == [supplier, person, "1254.75"]
    # Restore the masked bank account without Excel ever converting it to a number.
    restored_individual = tmp_path / "明细还原.csv"
    restore_csv(restore_configuration(files[0].output_path, (0, 1, 2)), restored_individual, repository)
    assert read_rows(restored_individual)[1][2] == account
    assert all(p.read_bytes() == content for p, content in originals.items())
    assert supplier.encode() not in repository.database_path.read_bytes()
    assert person.encode() not in repository.database_path.read_bytes()
    # A returned code that is not in our vault must stop, without a partial result.
    unknown = tmp_path / "未知代号.csv"
    write_rows(unknown, [["供应商", "人员"], ["SUPPLIER-AAAAAAAAAAAA", records[0][headers[1]]]])
    missing_output = tmp_path / "不应生成.csv"
    with pytest.raises(MissingCodeError):
        restore_csv(restore_configuration(unknown), missing_output, repository)
    assert not missing_output.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_real_windows_dpapi_protects_and_reloads_key(tmp_path):
    provider = LocalKeyProvider(tmp_path / "dpapi-smoke")
    key = provider.get_key()
    assert len(key) == 32
    assert provider.key_path.read_bytes() != key
    assert LocalKeyProvider(provider.key_path.parent).get_key() == key
