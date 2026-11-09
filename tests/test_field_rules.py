from dataclasses import replace
import os

import pytest

from data_mask_studio.anonymization import ColumnConfig, generate_token, validate_configuration
from data_mask_studio.batch import BatchFile, BatchFileStatus, BatchService
from data_mask_studio.csv_tools import inspect_csv, anonymize_csv
from data_mask_studio.field_rules import (
    FieldRule, FieldRuleSet, FieldRuleError, FieldRuleRepository, ConfirmedFieldRules,
    RuleAwareProfiles, review_file,
)
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.restoration import restore_csv
from data_mask_studio.vault import VaultRepository, VaultCipher
from test_chinese_workflow import write_rows, read_rows, restore_configuration, FixedKeyProvider


def rules():
    return FieldRuleSet((FieldRule("主体名称", "AAAA", ("主体名称", "公司名称", "核算主体")),
                         FieldRule("科目名称", "AAA", ("科目名称", "会计科目"))))


def setup_batch(tmp_path, sources):
    profiles = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    batch = BatchService(RuleAwareProfiles(profiles))
    files = [BatchFile(path) for path in sources]
    batch.validate(files, rules())
    return batch, files


@pytest.mark.parametrize("encoding", ["gbk", "gb18030"])
def test_excel_ansi_reports_match_chinese_rules_and_returned_reports_restore(tmp_path, encoding):
    import csv
    from io import StringIO

    policy = FieldRuleSet((FieldRule("店名", "AAA", ("店名",)), FieldRule("品种", "BBB", ("品种",))))
    sources = []
    tables = [
        [["日期", "店名", "品种", "金额"], ["2026-11-05", "合成甲店", "合成品种", "0010.50"]],
        [["店名", "备注", "品种"], ["合成甲店", "合成,备注\n第二行", "合成品种"]],
    ]
    for index, rows in enumerate(tables):
        stream = StringIO(newline="")
        csv.writer(stream).writerows(rows)
        path = tmp_path / f"report-{index}.csv"
        path.write_bytes(stream.getvalue().encode(encoding))
        sources.append(path)
    originals = [path.read_bytes() for path in sources]
    batch = BatchService(RuleAwareProfiles(ProfileService(ProfileRepository(tmp_path / "profiles.json"))))
    files = [BatchFile(path) for path in sources]
    batch.validate(files, policy)
    assert all(item.status is BatchFileStatus.COMPATIBLE and item.encoding == "gb18030" for item in files)
    confirmed = ConfirmedFieldRules(policy, tuple(item.field_review for item in files))
    vault = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    output = tmp_path / "output"
    output.mkdir()
    batch.process(files, confirmed, output, FixedKeyProvider(), lambda: vault)
    assert all(item.status is BatchFileStatus.COMPLETED for item in files)
    masked = [read_rows(item.output_path) for item in files]
    assert masked[0][1][1] == masked[1][1][0]
    assert masked[0][1][2] == masked[1][1][2]
    for index, selected in enumerate(((1, 2), (0, 2))):
        returned = tmp_path / f"returned-{index}.csv"
        returned.write_bytes(files[index].output_path.read_text(encoding="utf-8-sig").encode(encoding))
        restored = tmp_path / f"restored-{index}.csv"
        restore_csv(restore_configuration(returned, selected), restored, vault)
        assert read_rows(restored) == tables[index]
    assert [path.read_bytes() for path in sources] == originals


def test_different_structures_shared_categories_restore_and_preserve_columns(tmp_path):
    first, second = tmp_path / "余额表.csv", tmp_path / "明细表.csv"
    write_rows(first, [["核算主体", "科目名称", "期初余额"], ["合成公司甲", "合成科目", "0010.50"]])
    write_rows(second, [["日期", "公司名称", "会计科目", "对方备注", "主体名称"],
                        ["2026-11-05", "合成公司甲", "合成科目", "保留,明文\n备注", "合成公司甲"]])
    source_bytes = [path.read_bytes() for path in (first, second)]
    batch, files = setup_batch(tmp_path, (first, second))
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in files)
    assert [column.rule is None for column in files[1].field_review.columns] == [True, False, False, True, False]
    policy = ConfirmedFieldRules(rules(), tuple(item.field_review for item in files))
    vault = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    output = tmp_path / "output"
    output.mkdir()
    batch.process(files, policy, output, FixedKeyProvider(), lambda: vault)
    assert all(item.status is BatchFileStatus.COMPLETED for item in files)
    rows1, rows2 = [read_rows(item.output_path) for item in files]
    assert rows1[0] == ["核算主体", "科目名称", "期初余额"]
    assert rows2[0] == ["日期", "公司名称", "会计科目", "对方备注", "主体名称"]
    assert rows1[1][0] == rows2[1][1] == rows2[1][4] == generate_token(FixedKeyProvider().get_key(), "AAAA", "合成公司甲")
    assert rows1[1][1] == rows2[1][2]
    assert rows1[1][2] == "0010.50"
    assert rows2[1][0] == "2026-11-05" and rows2[1][3] == "保留,明文\n备注"
    for item, selected, original in [(files[0], (0, 1), first), (files[1], (1, 2, 4), second)]:
        restored = tmp_path / (original.stem + "_restored.csv")
        restore_csv(restore_configuration(item.output_path, selected), restored, vault)
        assert read_rows(restored) == read_rows(original)
    assert [path.read_bytes() for path in (first, second)] == source_bytes


def test_optional_missing_category_and_zero_match_are_distinct(tmp_path):
    first, second = tmp_path / "主体表.csv", tmp_path / "未识别.csv"
    write_rows(first, [["公司名称", "金额"], ["合成甲", "10"]])
    write_rows(second, [["其他字段", "金额"], ["合成甲", "10"]])
    _, files = setup_batch(tmp_path, (first, second))
    assert files[0].status is BatchFileStatus.COMPATIBLE
    assert files[1].status is BatchFileStatus.INCOMPATIBLE
    assert files[1].rule_error_id == "field_rules.error.no_match"
    assert files[1].field_review is None


def test_raw_rules_cannot_process_without_confirmation(tmp_path):
    source = tmp_path / "source.csv"
    write_rows(source, [["主体名称"], ["合成甲"]])
    batch, files = setup_batch(tmp_path, (source,))
    with pytest.raises(FieldRuleError, match="field_rules.error.confirm_first"):
        batch.process(files, rules(), tmp_path, None, lambda: pytest.fail("No vault access before confirmation"))
    assert not list(tmp_path.glob("*_masked.csv"))


@pytest.mark.parametrize("change", ["value", "header", "new_file", "rule"])
def test_changes_after_review_block_before_key_access(tmp_path, change):
    source = tmp_path / "source.csv"
    write_rows(source, [["主体名称", "金额"], ["合成甲", "10"]])
    batch, files = setup_batch(tmp_path, (source,))
    policy = ConfirmedFieldRules(rules(), (files[0].field_review,))
    if change in ("value", "header"):
        stamp = source.stat()
        write_rows(source, [["主体名称" if change == "value" else "公司名称", "金额"], ["合成乙", "10"]])
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    elif change == "new_file":
        files.append(BatchFile(tmp_path / "extra.csv"))
    else:
        changed = FieldRuleSet((FieldRule("主体名称", "CHANGED", ("主体名称",)),))
        policy = replace(policy, rules=changed)
    with pytest.raises(FieldRuleError):
        batch.process(files, policy, tmp_path, None, lambda: pytest.fail("Unreviewed data must not access vault"))
    assert not list(tmp_path.glob("*_masked.csv"))


def test_change_during_processing_rolls_back_mappings_and_output(tmp_path):
    source = tmp_path / "source.csv"
    write_rows(source, [["主体名称", "金额"], ["合成甲", "10"], ["合成乙", "20"]])
    review = review_file(rules(), inspect_csv(source))
    vault = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    destination = tmp_path / "out.csv"
    changed = []

    def progress(_):
        if not changed:
            changed.append(True)
            write_rows(source, [["主体名称", "金额"], ["合成丙", "30"]])

    from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
    with pytest.raises(CSVAnonymizationError):
        anonymize_csv(source, destination, encoding=review.encoding, delimiter=review.delimiter,
                      processing_plan=review.plan, secret_key=FixedKeyProvider().get_key(),
                      vault_repository=vault, progress_callback=progress,
                      expected_source_fingerprint={"sha256": review.sha256, "size": review.size})
    assert not destination.exists()
    assert vault.count() == 0


def test_source_identity_swapped_after_preflight_cannot_drop_fingerprint_guard(tmp_path):
    source, replacement = tmp_path / "approved.csv", tmp_path / "replacement.csv"
    write_rows(source, [["主体名称"], ["合成甲"]])
    write_rows(replacement, [["主体名称"], ["未确认的合成乙"]])
    batch, files = setup_batch(tmp_path, (source,))
    policy = ConfirmedFieldRules(rules(), (files[0].field_review,))
    vault = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    output = tmp_path / "output"
    output.mkdir()

    class SwappingKeyProvider:
        def get_key(self):
            files[0].path = replacement
            return FixedKeyProvider().get_key()

    result = batch.process(files, policy, output, SwappingKeyProvider(), lambda: vault)
    assert result.error_files == 1
    assert files[0].rule_error_id == "field_rules.error.changed"
    assert vault.count() == 0
    assert not list(output.glob("*.csv"))


@pytest.mark.parametrize("bad", [
    (FieldRule("A", "AA", ("公司名称",)), FieldRule("B", "BB", (" 公司名称 ",))),
    (FieldRule("A", "AA", ("Company Name",)), FieldRule("B", "BB", (" company  NAME ",))),
    (FieldRule("A", "AA", ("A",)), FieldRule("B", "AA", ("B",))),
    (FieldRule("A", "AA", ("A",)), FieldRule("A", "BB", ("B",))),
    (FieldRule("A", "AA", ()),),
    (FieldRule("A", "bad-prefix", ("A",)),),
])
def test_conflicting_or_invalid_rules_cannot_be_saved(bad):
    with pytest.raises(FieldRuleError):
        FieldRuleSet(bad)


def test_repository_roundtrip_concurrent_update_and_corrupt_file_preservation(tmp_path):
    repository = FieldRuleRepository(tmp_path / "rules.json")
    assert repository.load() == FieldRuleSet() and not repository.path.exists()
    repository.save(rules(), expected=FieldRuleSet())
    assert repository.load() == rules()
    before = repository.path.read_bytes()
    with pytest.raises(FieldRuleError, match="field_rules.error.concurrent"):
        repository.save(FieldRuleSet(), expected=FieldRuleSet())
    assert repository.path.read_bytes() == before
    repository.path.write_text('{"schema_version":1,"rules":[],"rules":[]}', encoding="utf-8")
    corrupted = repository.path.read_bytes()
    with pytest.raises(FieldRuleError):
        repository.save(rules(), expected=FieldRuleSet())
    assert repository.path.read_bytes() == corrupted


def test_legacy_profiles_still_require_structure_and_unique_prefixes(tmp_path):
    configs = [ColumnConfig("主体名称", True, "AAAA"), ColumnConfig("公司名称", True, "AAAA")]
    assert not validate_configuration(configs).is_valid
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("严格方案", [ColumnConfig("主体名称", True, "AAAA")])
    source = tmp_path / "extra.csv"
    write_rows(source, [["主体名称", "额外列"], ["合成甲", "x"]])
    from data_mask_studio.processing.planner import PlanningError
    with pytest.raises(PlanningError):
        RuleAwareProfiles(service).build_plan(profile, inspect_csv(source))
