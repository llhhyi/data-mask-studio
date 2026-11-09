from dataclasses import dataclass, field
from pathlib import Path


class NarrativeError(ValueError):
    def __init__(self, message_id: str, *, codes: tuple[str, ...] = ()):
        self.message_id, self.codes = message_id, codes
        super().__init__(message_id)


class NarrativeCancelled(NarrativeError):
    def __init__(self):
        super().__init__("narrative.error.cancelled")


class NarrativeMissingCodes(NarrativeError):
    def __init__(self, codes):
        super().__init__("narrative.error.missing", codes=tuple(codes))


@dataclass(frozen=True, slots=True)
class NarrativeResult:
    total_occurrences: int
    found_codes: int
    missing_codes: int
    skipped_occurrences: int
    invalid_occurrences: int
    missing: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    restored_occurrences: int = 0
    text: str | None = field(default=None, repr=False)
    output_path: Path | None = None
    image_count: int = 0


@dataclass(slots=True)
class TextUnit:
    text: str = field(repr=False)
    apply: object = field(repr=False)
    markdown: bool = False
