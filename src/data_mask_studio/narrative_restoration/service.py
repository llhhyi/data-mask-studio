"""Local, read-only restoration for narratives, JSON strings and Word packages."""

from collections import Counter
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from data_mask_studio import environment
from data_mask_studio.consultant.code_parser import is_valid_code
from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file
from data_mask_studio.publication import fingerprint, publish, PublicationError
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.restoration.exceptions import RestorationSecurityError

from .markdown import escape_value, protected_ranges
from .models import NarrativeCancelled, NarrativeError, NarrativeMissingCodes, NarrativeResult, TextUnit
from .word import WordDocument

TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]{1,23}-[A-Z2-7]{12}(?![A-Za-z0-9_=])")
SIMILAR = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]{1,23}-[A-Za-z0-9_]{1,32}(?![A-Za-z0-9_=])")
JSON_STRING = re.compile(r'"(?:[^"\\]|\\[\s\S])*"')
JSON_KEY_SUFFIX = re.compile(r"\s*:")
FORMATS = {".md": "markdown", ".markdown": "markdown", ".docx": "word", ".json": "json"}
MAX_TEXT = 16 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024


def _patch_text(text, patches):
    for start, end, value in reversed(patches):
        text = text[:start] + value + text[end:]
    return text


def _json_document(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError

    try:
        json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, RecursionError):
        raise NarrativeError("narrative.error.json") from None


class _TextDocument:
    def __init__(self, text, mode):
        self.text, self.patches = text, []
        self.units, self.skipped = [], []
        self.image_count = 0
        if mode == "json":
            _json_document(text)
            for match in JSON_STRING.finditer(text):
                value = json.loads(match[0])
                if JSON_KEY_SUFFIX.match(text, match.end()):
                    self.skipped.append(value)
                    continue
                def apply(patches, match=match, value=value):
                    if patches:
                        self.patches.append((match.start(), match.end(), json.dumps(_patch_text(value, patches), ensure_ascii=False)))
                self.units.append(TextUnit(value, apply))
        elif mode == "markdown":
            start = 0
            for left, right in protected_ranges(text):
                self._unit(start, left, True)
                self.skipped.append(text[left:right])
                start = right
            self._unit(start, len(text), True)
        elif mode == "plain":
            self._unit(0, len(text), False)
        else:
            raise NarrativeError("narrative.error.format")
        self.mode = mode

    def _unit(self, start, end, markdown):
        if start < end:
            self.units.append(TextUnit(self.text[start:end], lambda patches, start=start:
                                       self.patches.extend((a + start, b + start, value) for a, b, value in patches), markdown))

    def serialize(self, _check):
        result = _patch_text(self.text, sorted(self.patches))
        if self.mode == "json":
            _json_document(result)
        return result


def _unicode_text(raw):
    try:
        if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            return raw.decode("utf-32")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeError:
        raise NarrativeError("narrative.error.encoding") from None


class NarrativeRestorationService:
    def __init__(self, repository_factory):
        self.repository_factory = repository_factory

    def analyze_file(self, path, *, should_cancel=None, **_kwargs):
        return self._file(path, should_cancel=should_cancel, analyze=True)

    def restore_file(self, path, destination, *, should_cancel=None,
                     representation_policy=RepresentationPolicy.FIRST_ORIGINAL, **_kwargs):
        return self._file(path, destination, should_cancel=should_cancel,
                          representation_policy=representation_policy)

    def restore_text(self, text, *, mode="markdown", analyze=False, should_cancel=None,
                     representation_policy=RepresentationPolicy.FIRST_ORIGINAL):
        check = lambda: self._check(should_cancel)
        self._validate_text(text)
        document = _TextDocument(text, mode)
        result = self._process(document, analyze, check, representation_policy)
        return result if analyze else replace(result, text=document.serialize(check))

    def _file(self, path, destination=None, *, should_cancel=None, analyze=False,
              representation_policy=RepresentationPolicy.FIRST_ORIGINAL):
        check = lambda: self._check(should_cancel)
        check()
        source = Path(path).expanduser().absolute()
        mode = FORMATS.get(source.suffix.casefold())
        if mode is None:
            raise NarrativeError("narrative.error.format")
        temporary = None
        try:
            if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_FILE:
                raise NarrativeError("narrative.error.size")
            with source.open("rb") as stream:
                raw = stream.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                raise NarrativeError("narrative.error.size")
            original = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            if mode == "word":
                document = WordDocument(raw, check)
            else:
                text = _unicode_text(raw)
                self._validate_text(text)
                document = _TextDocument(text, mode)
            result = self._process(document, analyze, check, representation_policy)
            check()
            if fingerprint(source) != original:
                raise NarrativeError("narrative.error.changed")
            if analyze:
                return result
            target = Path(destination).expanduser().absolute()
            if (target.suffix.casefold() != source.suffix.casefold()
                    or paths_refer_to_same_file(source, target) or target.is_symlink()):
                raise NarrativeError("narrative.error.destination")
            if not target.parent.is_dir() or target.exists():
                raise NarrativeError("narrative.error.destination")
            output = document.serialize(check)
            output = output.encode("utf-8") if isinstance(output, str) else output
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".dms-narrative-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
            check()
            if fingerprint(source) != original:
                raise NarrativeError("narrative.error.changed")
            publish(temporary, target, overwrite=False)
            temporary = None
            return replace(result, output_path=target)
        except (OSError, PublicationError):
            raise NarrativeError("narrative.error.io") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _process(self, document, analyze, check, representation):
        matches = []
        invalid = 0
        for unit in document.units:
            check()
            matches.append(tuple(TOKEN.finditer(unit.text)))
            invalid += sum(not is_valid_code(match[0]) for match in SIMILAR.finditer(unit.text))
        counts = Counter(match[0] for group in matches for match in group)
        skipped = Counter(match[0] for text in document.skipped for match in TOKEN.finditer(text))
        if len(counts) > 100000:
            raise NarrativeError("narrative.error.size")
        mappings = {}
        if counts:
            repository = self.repository_factory().as_read_only()
            try:
                with environment.environment_lease(repository.database_path.parent):
                    with repository.read_session() as session:
                        codes = tuple(counts)
                        for offset in range(0, len(codes), 500):
                            check()
                            mappings.update(session.get_many_with_composites(codes[offset:offset + 500]))
            except NarrativeCancelled:
                raise
            except Exception as error:
                raise RestorationSecurityError("Unable to read the local mapping vault safely.") from error
        if any(hasattr(value, "canonical_values") for value in mappings.values()):
            raise NarrativeError("narrative.error.composite")
        missing = tuple(code for code in counts if code not in mappings)
        result = NarrativeResult(sum(counts.values()), sum(count for code, count in counts.items() if code in mappings),
                                 sum(counts[code] for code in missing), sum(skipped.values()), invalid,
                                 missing, tuple(skipped), image_count=document.image_count)
        if analyze:
            return result
        if missing:
            raise NarrativeMissingCodes(missing)
        if not counts and not skipped:
            raise NarrativeError("narrative.error.no_codes")
        for unit, group in zip(document.units, matches, strict=True):
            check()
            patches = []
            for match in group:
                mapping = mappings[match[0]]
                value = (mapping.canonical_value if representation is RepresentationPolicy.CANONICAL else mapping.original_value)
                if not isinstance(value, str):
                    raise NarrativeError("narrative.error.value")
                patches.append((match.start(), match.end(), escape_value(value) if unit.markdown else value))
            unit.apply(patches)
        return replace(result, restored_occurrences=result.found_codes)

    @staticmethod
    def _check(should_cancel):
        if should_cancel and should_cancel():
            raise NarrativeCancelled()

    @staticmethod
    def _validate_text(text):
        if not isinstance(text, str) or not text or len(text.encode("utf-8", errors="surrogatepass")) > MAX_TEXT or "\x00" in text:
            raise NarrativeError("narrative.error.size")
