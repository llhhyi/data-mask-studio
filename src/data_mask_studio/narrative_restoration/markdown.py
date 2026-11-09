"""Identify literal Markdown regions which the user has excluded from restoration."""

import html
import re


def protected_ranges(text: str) -> list[tuple[int, int]]:
    spans = []
    offset = 0
    fence = None
    for line in text.splitlines(keepends=True):
        stripped = re.sub(r"^(?:[ \t]{0,3}>[ \t]?)*", "", line)
        marker = re.match(r"^[ \t]*(?:[-+*] |\d+[.)] )?(`{3,}|~{3,})", stripped)
        if fence is not None:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                    and not stripped[marker.end():].strip()):
                spans.append((fence[2], offset + len(line)))
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]), offset)
        elif line.startswith(("    ", "\t")):
            spans.append((offset, offset + len(line)))
        offset += len(line)
    if fence is not None:
        spans.append((fence[2], len(text)))
    for pattern in (
        r"<!--[\s\S]*?(?:-->|\Z)",
        r"<(script|style|pre|code)\b[^>]*>[\s\S]*?(?:</\1\s*>|\Z)",
        r"<[^>\n]*>",
        r"[a-z][a-z0-9+.-]*://[^\s<>]+",
        r"(?m)^[ \t]{0,3}\[[^\]\n]+\]:[^\n]*",
    ):
        spans.extend(match.span() for match in re.finditer(pattern, text, re.IGNORECASE))
    ticks = list(re.finditer(r"`+", text))
    next_same, following = {}, {}
    for index in range(len(ticks) - 1, -1, -1):
        length = len(ticks[index][0])
        next_same[index] = following.get(length)
        following[length] = index
    index = 0
    while index < len(ticks):
        close = next_same[index]
        if close is None:
            index += 1
        else:
            spans.append((ticks[index].start(), ticks[close].end()))
            index = close + 1
    # Parentheses in link destinations may nest, and escaped delimiters are literal.
    covered_until = 0
    for start in re.finditer(r"\]\(", text):
        if start.start() < covered_until:
            continue
        depth, index = 1, start.end()
        while index < len(text) and depth:
            if text[index] == "\\":
                index += 2
                continue
            depth += (text[index] == "(") - (text[index] == ")")
            index += 1
        spans.append((start.end(), index))
        covered_until = index
    # Reference identifiers are structural; restoring one side would break the link.
    for match in re.finditer(r"\]\[([^\]\n]*)\]", text):
        spans.append((match.start(1), match.end(1)))
    labels = set(re.findall(r"(?m)^[ \t]{0,3}\[([^\]\n]+)\]:", text))
    for match in re.finditer(r"\[([^\]\n]+)\]", text):
        if match[1] in labels:
            spans.append(match.span(1))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def escape_value(value: str) -> str:
    value = html.escape(value, quote=False)
    value = re.sub(r"([\\`*_{\[\]}()#+.!|~>\-])", r"\\\1", value)
    return value.replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")
