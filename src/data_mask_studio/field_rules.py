"""Explicit column aliases, stable token namespaces, and content-bound reviews."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.anonymization.prefix_rules import validate_prefix
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.csv_tools.models import CSVInspectionResult
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.models import ProcessingPlan
from data_mask_studio.processing.planner import build_processing_plan, PlanningError
from data_mask_studio.profiles import ProfileApplicationResult, ProfileColumn
from data_mask_studio.profiles.locking import profile_write_lock
from data_mask_studio.profiles.exceptions import ProfileStorageError
from data_mask_studio.publication import fingerprint, PublicationError


class FieldRuleError(PlanningError):
    def __init__(self, message_id: str):
        self.message_id = message_id
        super().__init__(message_id)


def alias_key(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass(frozen=True, slots=True)
class FieldRule:
    name: str
    prefix: str
    aliases: tuple[str, ...]
    normalization: NormalizationRule = NormalizationRule.EXACT


@dataclass(frozen=True, slots=True)
class FieldRuleSet:
    rules: tuple[FieldRule, ...] = ()

    def __post_init__(self):
        if type(self.rules) is not tuple or len(self.rules) > 200:
            raise FieldRuleError("field_rules.error.invalid")
        names, prefixes, aliases = set(), set(), set()
        for rule in self.rules:
            if (not isinstance(rule, FieldRule) or not isinstance(rule.name, str)
                    or not rule.name.strip() or len(rule.name) > 100
                    or any(ord(char) < 32 for char in rule.name)
                    or not isinstance(rule.prefix, str) or validate_prefix(rule.prefix)
                    or not isinstance(rule.normalization, NormalizationRule)
                    or type(rule.aliases) is not tuple or not 1 <= len(rule.aliases) <= 200):
                raise FieldRuleError("field_rules.error.invalid")
            if alias_key(rule.name) in names or rule.prefix in prefixes:
                raise FieldRuleError("field_rules.error.conflict")
            names.add(alias_key(rule.name))
            prefixes.add(rule.prefix)
            for alias in rule.aliases:
                if (not isinstance(alias, str) or not alias.strip() or len(alias) > 500
                        or any(ord(char) < 32 for char in alias)):
                    raise FieldRuleError("field_rules.error.invalid")
                if alias_key(alias) in aliases:
                    raise FieldRuleError("field_rules.error.conflict")
                aliases.add(alias_key(alias))


@dataclass(frozen=True, slots=True)
class ReviewedColumn:
    index: int
    header: str
    rule: FieldRule | None


@dataclass(frozen=True, slots=True)
class FieldFileReview:
    path: Path
    sha256: str
    size: int
    encoding: str
    delimiter: str
    columns: tuple[ReviewedColumn, ...]
    plan: ProcessingPlan


@dataclass(frozen=True, slots=True)
class ConfirmedFieldRules:
    rules: FieldRuleSet
    files: tuple[FieldFileReview, ...]


def match_columns(rules: FieldRuleSet, inspection: CSVInspectionResult) -> tuple[ReviewedColumn, ...]:
    aliases = {alias_key(alias): rule for rule in rules.rules for alias in rule.aliases}
    synthetic = {replacement.position - 1 for replacement in inspection.header_replacements}
    return tuple(ReviewedColumn(index, header, None if index in synthetic else aliases.get(alias_key(header)))
                 for index, header in enumerate(inspection.headers))


def rule_plan(rules: FieldRuleSet, inspection: CSVInspectionResult) -> ProcessingPlan:
    columns = match_columns(rules, inspection)
    if not any(column.rule for column in columns):
        raise FieldRuleError("field_rules.error.no_match")
    configs = [ColumnConfig(column.header, action=ColumnAction.MASK if column.rule else ColumnAction.PRESERVE,
                            prefix=column.rule.prefix if column.rule else "",
                            normalization_rule=column.rule.normalization if column.rule else NormalizationRule.EXACT)
               for column in columns]
    return build_processing_plan(inspection, configs,
                                 shared_prefixes=frozenset(rule.prefix for rule in rules.rules))


def review_file(rules: FieldRuleSet, inspection: CSVInspectionResult) -> FieldFileReview:
    plan = rule_plan(rules, inspection)
    before = fingerprint(inspection.path)
    current = inspect_csv(inspection.path)
    after = fingerprint(inspection.path)
    if (before != after or current.headers != inspection.headers
            or current.header_replacements != inspection.header_replacements
            or current.encoding != inspection.encoding or current.delimiter != inspection.delimiter):
        raise FieldRuleError("field_rules.error.changed")
    return FieldFileReview(inspection.path.resolve(), after["sha256"], after["size"],
                           inspection.encoding, inspection.delimiter, match_columns(rules, inspection), plan)


def confirmed_review(policy: ConfirmedFieldRules, path: Path) -> FieldFileReview:
    matches = [item for item in policy.files if item.path == path.resolve()]
    if len(matches) != 1:
        raise FieldRuleError("field_rules.error.changed")
    item = matches[0]
    try:
        current = fingerprint(path)
    except PublicationError:
        raise FieldRuleError("field_rules.error.changed") from None
    if current != {"sha256": item.sha256, "size": item.size}:
        raise FieldRuleError("field_rules.error.changed")
    return item


class RuleAwareProfiles:
    """Adapt only the new mode; legacy profile planning keeps its strict policy."""
    def __init__(self, profiles):
        self.profiles = profiles

    def apply(self, policy, inspection):
        if not isinstance(policy, (FieldRuleSet, ConfirmedFieldRules)):
            return self.profiles.apply(policy, inspection)
        rules = policy.rules if isinstance(policy, ConfirmedFieldRules) else policy
        columns = match_columns(rules, inspection)
        return ProfileApplicationResult(
            tuple(ProfileColumn(column.header, column.rule.prefix if column.rule else "",
                                column.rule.normalization if column.rule else NormalizationRule.EXACT,
                                action=ColumnAction.MASK if column.rule else ColumnAction.PRESERVE)
                  for column in columns),
            tuple(column.header for column in columns if column.rule), (),
        )

    def build_plan(self, policy, inspection):
        if isinstance(policy, ConfirmedFieldRules):
            review = confirmed_review(policy, inspection.path)
            if (tuple(inspection.headers) != tuple(column.header for column in review.columns)
                    or inspection.encoding != review.encoding or inspection.delimiter != review.delimiter
                    or review.plan != rule_plan(policy.rules, inspection)):
                raise FieldRuleError("field_rules.error.changed")
            return review.plan
        if isinstance(policy, FieldRuleSet):
            return rule_plan(policy, inspection)
        return self.profiles.build_plan(policy, inspection)


class FieldRuleRepository:
    def __init__(self, path: Path | None = None):
        local = os.environ.get("LOCALAPPDATA")
        self.path = Path(path) if path is not None else (Path(local) / "DataMaskStudio" / "field_rules.json" if local else None)

    def load(self) -> FieldRuleSet:
        try:
            if self.path is None or self.path.is_symlink() or self.path.is_junction():
                raise FieldRuleError("field_rules.error.storage")
            with self.path.open("rb") as stream:
                raw = stream.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
            if (type(document) is not dict or set(document) != {"schema_version", "rules"}
                    or type(document["schema_version"]) is not int or document["schema_version"] != 1
                    or type(document["rules"]) is not list):
                raise ValueError
            result = []
            for row in document["rules"]:
                if (type(row) is not dict or set(row) != {"name", "prefix", "aliases", "normalization"}
                        or type(row["aliases"]) is not list):
                    raise ValueError
                result.append(FieldRule(row["name"], row["prefix"], tuple(row["aliases"]), NormalizationRule(row["normalization"])))
            return FieldRuleSet(tuple(result))
        except FileNotFoundError:
            return FieldRuleSet()
        except FieldRuleError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
            raise FieldRuleError("field_rules.error.storage") from None

    def save(self, rules: FieldRuleSet, *, expected: FieldRuleSet):
        if self.path is None:
            raise FieldRuleError("field_rules.error.storage")
        temporary = None
        try:
            with profile_write_lock(self.path):
                if self.load() != expected:
                    raise FieldRuleError("field_rules.error.concurrent")
                document = {"schema_version": 1, "rules": [
                    {"name": rule.name, "prefix": rule.prefix, "aliases": list(rule.aliases), "normalization": rule.normalization.value}
                    for rule in rules.rules]}
                raw = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
                if len(raw) > 512 * 1024:
                    raise FieldRuleError("field_rules.error.invalid")
                descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix=".field-rules-", suffix=".tmp")
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                temporary = None
        except FieldRuleError:
            raise
        except (OSError, ProfileStorageError):
            raise FieldRuleError("field_rules.error.storage") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result
