from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from data_mask_studio.anonymization import generate_token
from data_mask_studio.narrative_restoration import (
    NarrativeCancelled, NarrativeError, NarrativeMissingCodes, NarrativeRestorationService,
)
from data_mask_studio.narrative_restoration.markdown import escape_value
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

KEY = b"H" * 32
VALUE = '合成甲公司 & 分部|A[测试]'
CODE = generate_token(KEY, "ENTITY", VALUE)
SECOND = generate_token(KEY, "PERSON", "合成人员乙")
MISSING = "UNKNOWN-ABCDEFGHI234"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@pytest.fixture
def case(tmp_path):
    vault = VaultRepository(tmp_path / "vault.db", VaultCipher(b"V" * 32))
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(CODE, "ENTITY", VALUE, "公司名称"),
                                  MappingCandidate(SECOND, "PERSON", "合成人员乙", "姓名")])
    return NarrativeRestorationService(lambda: vault), vault


def word_bytes(code=CODE):
    """A small package fixture, with explicit style runs and independent story parts."""
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "word/document.xml": f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>结论：{code[:10]}</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>{code[10:]}明显增长</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>{code}</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:pict><w:txbxContent><w:p><w:r><w:t>{code}</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p><w:sectPr/></w:body></w:document>',
        "word/styles.xml": f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Normal"/></w:styles>',
        "word/media/image1.png": b"unchanged fixture media",
        "word/_rels/document.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="hyperlink" Target="https://example.invalid/' + code + '" TargetMode="External"/></Relationships>',
    }
    for name, tag, body in [("header1", "hdr", ""), ("footer1", "ftr", ""), ("footnotes", "footnotes", "footnote"), ("endnotes", "endnotes", "endnote")]:
        content = f'<w:p><w:r><w:t>{code}</w:t></w:r></w:p>'
        if body:
            content = f'<w:{body} w:id="1">{content}</w:{body}>'
        parts[f"word/{name}.xml"] = f'<w:{tag} xmlns:w="{W}">{content}</w:{tag}>'
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value.encode() if isinstance(value, str) else value)
    return output.getvalue()


def test_plain_text_exact_replacement_repetition_chinese_boundaries_and_no_repair(case):
    service, vault = case
    before = vault.database_path.read_bytes()
    broken = CODE[:12] + "\n" + CODE[12:]
    text = f"本月{CODE}增长，{SECOND}审核。{CODE}\n{broken} / {CODE.lower()} / x{CODE}"
    result = service.restore_text(text, mode="plain")
    assert result.restored_occurrences == 3
    assert result.text == text.replace(CODE, VALUE, 2).replace(SECOND, "合成人员乙")
    assert vault.database_path.read_bytes() == before


def test_unknown_code_blocks_all_output_and_lists_codes(case):
    service, _ = case
    text = f"{CODE}，{MISSING}"
    analysis = service.restore_text(text, mode="plain", analyze=True)
    assert analysis.missing == (MISSING,) and analysis.text is None
    with pytest.raises(NarrativeMissingCodes) as caught:
        service.restore_text(text, mode="plain")
    assert caught.value.codes == (MISSING,)


def test_markdown_restores_prose_tables_and_keeps_code_link_destinations(case):
    service, _ = case
    text = f"# {CODE}\n\n|主体|人员|\n|---|---|\n|{CODE}|{SECOND}|\n\n```python\n{MISSING}\n```\n`{CODE}`\n[公司{CODE}](https://example.invalid/{CODE})\n<{CODE}>\n"
    result = service.restore_text(text, mode="markdown")
    assert result.restored_occurrences == 4
    assert result.skipped_occurrences == 4 and MISSING in result.skipped
    assert f"# {escape_value(VALUE)}" in result.text
    assert f"|{escape_value(VALUE)}|合成人员乙|" in result.text
    assert f"```python\n{MISSING}\n```" in result.text
    assert f"`{CODE}`" in result.text and f"https://example.invalid/{CODE}" in result.text


@pytest.mark.parametrize("region", [
    "~~~\n{code}\n~~~", "```\n{code}", "    {code}\n", "\t{code}\n",
    "> ```\n> {code}\n> ```", "- ```\n{code}\n```",
    "``{code}``", "[link](https://example.invalid/a(b)/{code})", "[label]: /{code}",
    "<script>{code}</script>", "<!-- {code} -->", '<span data-code="{code}">x</span>',
])
def test_markdown_excluded_regions_are_preserved(case, region):
    service, _ = case
    text = region.format(code=CODE)
    result = service.restore_text(text, mode="markdown")
    assert result.text == text and result.skipped_occurrences >= 1


def test_json_restores_only_values_keeps_keys_types_whitespace_and_escapes(case):
    service, _ = case
    text = '{\n  "' + CODE + '": "' + CODE + '", "nested": ["说明' + SECOND + '", 1.2300, true, null], "escaped": "ENTITY-"\n}'
    result = service.restore_text(text, mode="json")
    assert json.loads(result.text) == {CODE: VALUE, "nested": ["说明合成人员乙", 1.23, True, None], "escaped": "ENTITY-"}
    assert '1.2300, true, null' in result.text and result.text.startswith('{\n  "' + CODE + '": ')
    assert result.skipped_occurrences == 1 and result.restored_occurrences == 2


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{invalid}', '["unterminated]'])
def test_invalid_or_ambiguous_json_is_rejected(case, text):
    with pytest.raises(NarrativeError, match="narrative.error.json"):
        case[0].restore_text(text, mode="json")


def test_word_restores_every_story_and_split_runs_preserving_styles_and_assets(case, tmp_path):
    from defusedxml.minidom import parseString
    service, vault = case
    source, target = tmp_path / "AI.docx", tmp_path / "restored.docx"
    raw = word_bytes()
    source.write_bytes(raw)
    before = vault.database_path.read_bytes()
    analysis = service.analyze_file(source)
    assert analysis.total_occurrences == 7 and analysis.skipped_occurrences == 1
    result = service.restore_file(source, target)
    assert result.restored_occurrences == 7 and result.output_path == target
    with ZipFile(BytesIO(raw)) as old, ZipFile(target) as new:
        assert old.namelist() == new.namelist()
        for name in old.namelist():
            if name in {"word/styles.xml", "word/media/image1.png", "word/_rels/document.xml.rels", "[Content_Types].xml"}:
                assert old.read(name) == new.read(name)
            elif name.startswith("word/"):
                document = parseString(new.read(name))
                visible = ''.join(child.data for node in document.getElementsByTagNameNS(W, "t") for child in node.childNodes)
                assert VALUE in visible and CODE not in visible
        body = parseString(new.read("word/document.xml"))
        assert len(body.getElementsByTagNameNS(W, "b")) == 1
        assert len(body.getElementsByTagNameNS(W, "i")) == 1
    assert source.read_bytes() == raw and vault.database_path.read_bytes() == before


@pytest.mark.parametrize('marker', ['br', 'tab', 'noBreakHyphen', 'footnoteReference'])
def test_word_does_not_join_tokens_across_actual_breaks(case, tmp_path, marker):
    source = tmp_path / "break.docx"
    raw = word_bytes()
    with ZipFile(BytesIO(raw)) as archive:
        parts = {info.filename: archive.read(info) for info in archive.infolist()}
    parts['word/document.xml'] = parts['word/document.xml'].replace(b'</w:t></w:r><w:r><w:rPr><w:i/>', f'</w:t><w:{marker}/></w:r><w:r><w:rPr><w:i/>'.encode())
    with ZipFile(source, 'w') as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    assert case[0].analyze_file(source).total_occurrences == 6


@pytest.mark.parametrize("extension", [".md", ".json", ".docx"])
def test_unknown_does_not_publish_or_leave_temporary_file(case, tmp_path, extension):
    source, target = tmp_path / ('source' + extension), tmp_path / ('result' + extension)
    raw = word_bytes(MISSING) if extension == '.docx' else (json.dumps({"value": MISSING}) if extension == '.json' else MISSING).encode()
    source.write_bytes(raw)
    with pytest.raises(NarrativeMissingCodes):
        case[0].restore_file(source, target)
    assert not target.exists() and not list(tmp_path.glob('.dms-narrative-*'))


def test_existing_destination_and_source_alias_are_rejected(case, tmp_path):
    source, target = tmp_path / 'source.md', tmp_path / 'target.md'
    source.write_text(CODE, encoding='utf-8')
    target.write_bytes(b'keep')
    for destination in (source, target):
        with pytest.raises(NarrativeError, match='narrative.error.destination'):
            case[0].restore_file(source, destination)
    assert target.read_bytes() == b'keep' and source.read_text() == CODE


def test_cancelled_operations_leave_no_result(case):
    with pytest.raises(NarrativeCancelled):
        case[0].restore_text(CODE, mode='plain', should_cancel=lambda: True)


@pytest.mark.parametrize('extra', ['word/vbaProject.bin', '_xmlsignatures/sig1.xml'])
def test_macro_or_signed_word_is_rejected_without_output(case, tmp_path, extra):
    source, target = tmp_path / 'unsafe.docx', tmp_path / 'result.docx'
    source.write_bytes(word_bytes())
    with ZipFile(source, 'a') as archive:
        archive.writestr(extra, b'unsupported')
    with pytest.raises(NarrativeError, match='narrative.error.word'):
        case[0].restore_file(source, target)
    assert not target.exists()


def test_word_dtd_is_rejected_without_entity_expansion(case, tmp_path):
    source, target = tmp_path / 'entity.docx', tmp_path / 'result.docx'
    with ZipFile(BytesIO(word_bytes())) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['word/document.xml'] = b'<!DOCTYPE w:document [<!ENTITY x "blocked">]>' + parts['word/document.xml']
    with ZipFile(source, 'w') as archive:
        for name, raw in parts.items():
            archive.writestr(name, raw)
    with pytest.raises(NarrativeError, match='narrative.error.word'):
        case[0].restore_file(source, target)
    assert not target.exists()


def test_source_change_after_read_blocks_output(case, tmp_path, monkeypatch):
    service, _ = case
    source, target = tmp_path / 'changed.md', tmp_path / 'result.md'
    source.write_text(CODE, encoding='utf-8')
    original = service._process
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_text(SECOND, encoding='utf-8')
        return result
    monkeypatch.setattr(service, '_process', change)
    with pytest.raises(NarrativeError, match='narrative.error.changed'):
        service.restore_file(source, target)
    assert not target.exists() and not list(tmp_path.glob('.dms-narrative-*'))


def test_cancellation_after_mapping_lookup_leaves_no_file(case, tmp_path, monkeypatch):
    from threading import Event
    service, _ = case
    source, target = tmp_path / 'cancel.md', tmp_path / 'result.md'
    source.write_text(CODE, encoding='utf-8')
    cancelled = Event()
    original = service._process
    def stop(*args, **kwargs):
        result = original(*args, **kwargs)
        cancelled.set()
        return result
    monkeypatch.setattr(service, '_process', stop)
    with pytest.raises(NarrativeCancelled):
        service.restore_file(source, target, should_cancel=cancelled.is_set)
    assert not target.exists()


def test_json_quotes_newlines_and_escaped_codes_remain_valid(case):
    service, vault = case
    value = '合成"公司"\n\\路径\t尾部'
    code = generate_token(KEY, 'TEST', value)
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(code, 'TEST', value, '名称')])
    text = json.dumps({'key': code}).replace('TEST', '\\u0054EST')
    result = service.restore_text(text, mode='json')
    assert json.loads(result.text) == {'key': value}


def test_word_value_line_breaks_and_tabs_have_real_word_elements(case, tmp_path):
    from defusedxml.minidom import parseString
    service, vault = case
    value = '合成甲\n合成乙\t合成丙'
    code = generate_token(KEY, 'TEST', value)
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(code, 'TEST', value, '名称')])
    source, target = tmp_path / 'lines.docx', tmp_path / 'result.docx'
    source.write_bytes(word_bytes(code))
    service.restore_file(source, target)
    with ZipFile(target) as archive:
        doc = parseString(archive.read('word/document.xml'))
    assert len(doc.getElementsByTagNameNS(W, 'br')) == 3
    assert len(doc.getElementsByTagNameNS(W, 'tab')) == 3


def test_no_code_and_invalid_encoding_have_safe_localized_errors(case, tmp_path):
    from data_mask_studio.i18n import Translator
    with pytest.raises(NarrativeError) as caught:
        case[0].restore_text('没有代号的合成说明', mode='plain')
    assert '没有找到' in Translator('zh-CN').text(caught.value.message_id)
    source = tmp_path / 'legacy.md'
    source.write_bytes('合成中文'.encode('gbk'))
    with pytest.raises(NarrativeError, match='narrative.error.encoding'):
        case[0].analyze_file(source)


def test_word_default_namespace_keeps_line_breaks_and_tabs_valid(case, tmp_path):
    service, vault = case
    value = '合成甲\n合成乙\t合成丙'
    code = generate_token(KEY, 'TEST', value)
    with vault.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(code, 'TEST', value, '名称')])
    source, target = tmp_path / 'default.docx', tmp_path / 'result.docx'
    with ZipFile(BytesIO(word_bytes())) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['word/document.xml'] = (
        f'<document xmlns="{W}"><body><p><r><t>{code}</t></r></p></body></document>'
    ).encode()
    with ZipFile(source, 'w') as archive:
        for name, raw in parts.items():
            archive.writestr(name, raw)
    before = source.read_bytes()
    result = service.restore_file(source, target)
    from defusedxml.minidom import parseString
    with ZipFile(target) as archive:
        document = parseString(archive.read('word/document.xml'))
    assert result.restored_occurrences == 5
    assert len(document.getElementsByTagNameNS(W, 'br')) == 1
    assert len(document.getElementsByTagNameNS(W, 'tab')) == 1
    assert source.read_bytes() == before


@pytest.mark.parametrize('extension', ['.md', '.json', '.docx'])
@pytest.mark.parametrize('abort_batch', [False, True])
def test_narrative_batch_missing_code_respects_abort_scope(case, tmp_path, extension, abort_batch):
    from data_mask_studio.batch_restoration import (
        BatchMissingCodePolicy, BatchRestorationOptions, BatchRestorationService,
        BatchRestorationStatus, add_files,
    )
    _, vault = case
    first, second = tmp_path / ('unknown' + extension), tmp_path / 'known.md'
    raw = word_bytes(MISSING) if extension == '.docx' else (
        json.dumps({'value': MISSING}) if extension == '.json' else MISSING
    ).encode()
    first.write_bytes(raw)
    second.write_text(CODE, encoding='utf-8')
    output = tmp_path / 'output'
    output.mkdir()
    files = []
    add_files(files, [first, second])
    batch = BatchRestorationService(lambda: vault)
    batch.analyze_files(files)
    policy = BatchMissingCodePolicy.ABORT_BATCH if abort_batch else BatchMissingCodePolicy.ABORT_FILE
    summary = batch.restore_files(files, output, BatchRestorationOptions(missing_code_policy=policy))
    assert files[0].status is BatchRestorationStatus.ERROR
    assert files[0].narrative_error_codes == (MISSING,)
    assert files[0].output_path is None
    assert summary.error_files == 1
    if abort_batch:
        assert files[1].status is BatchRestorationStatus.SKIPPED
        assert summary.completed_files == 0 and summary.skipped_files == 1
        assert not list(output.iterdir())
    else:
        assert files[1].status is BatchRestorationStatus.COMPLETED
        assert summary.completed_files == 1 and summary.skipped_files == 0
        assert files[1].output_path.read_text(encoding='utf-8') == escape_value(VALUE)
        assert len(list(output.iterdir())) == 1
