"""Line-level evidence handles: chunks split into citable lines, handles parsed, echoes
checked.

The model cites lines by handle ([E3.2], or a range such as E3.2-E3.4) and never copies
text: the verifier extracts the cited lines from the chunk, so a quote cannot be misquoted
or invented. To catch a wrong number, the model also echoes the first words of the first
cited line, and the echo must match.

Segmentation is a pure function of the chunk text, so the same text always gets the same
handles and a recorded prompt can be rebuilt exactly (prompt_check).

- Free text (a verbatim slice of a note or a report's presentedForm; EvidenceChunk.free_text)
  is split on newlines. Each line is trimmed of surrounding whitespace, and blank lines get
  no handle; numbering runs over the non-empty lines. A line longer than MAX_LINE_CHARS is
  further split at sentence ends.
- Every other chunk (text rendered from coded resources, and request fields) is one line,
  whatever its layout, and so is a partial rule's computed fact [C1].

A sentence ends at ".", "!" or "?" followed by whitespace and then an uppercase letter, a
digit, or an opening bracket or quote; never after one of ABBREVIATIONS. Nothing else
splits a line: not semicolons, commas or a lowercase next word, so "5.5 mg" and "0-10)
that" stay whole.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from precertly.judgment.models import EvidenceChunk

# Derived from the line lengths of the facet fixtures' free text: the 228 free-text chunks
# across the 11 case bundles (54 distinct texts, 51 notes and 3 radiology reports) have 552
# distinct non-empty lines, median 42 characters, 90th percentile 194, maximum 548. The 478
# lines at or under 160 stay whole; the 74 over it are prose paragraphs that each carry
# several facts, which is what a line handle should separate. It is part of every prompt,
# so a change to it is a prompt change.
MAX_LINE_CHARS = 160

# A citation covers at most this many adjacent lines. Three covers a heading and two
# bullets, the most common multi-line pattern, and 619 of the 726 quotes verified in
# experiment 1.1 touch at most three lines. Lines further apart are cited separately.
MAX_CITATION_LINES = 3

# The model is asked for the first three words of the first cited line; the check accepts
# any prefix of at least ECHO_MIN_WORDS words. Measured on the experiment 1.1 prompts, a
# near-miss handle (an adjacent line, or the same line of a neighbouring item) passes a
# two-word echo 9.4% of the time and a three-word echo 8.3%, while models often echo two.
ECHO_MIN_WORDS = 2

ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "st", "vs", "e.g", "i.e", "etc", "approx", "no", "fig"}
)

_SENTENCE_END = re.compile(r"[.!?]\s+")
_OPENERS = "([\"'“‘"
_LAST_WORD = re.compile(r"(\S+)$")
_BULLET = re.compile(r"^\s*(?:[-*•‣◦–—]|\d+[.)])\s+")
_HANDLE = re.compile(
    r"""^\[?\s*(?P<item>[EC]\d+)(?:\.(?P<first>\d+))?\s*\]?
        (?:\s*[-–—]\s*\[?\s*(?:(?P<item2>[EC]?\d+)\.)?(?P<last>\d+)\s*\]?)?$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Line:
    number: int  # 1-based, as shown in the handle
    start: int  # offsets into the chunk text: text == chunk_text[start:end]
    end: int
    text: str


@dataclass(frozen=True)
class Handle:
    """A parsed citation handle. first and last are None for a bare item (E5, C1)."""

    item: str  # "E3" or "C1"
    first: int | None
    last: int | None


class HandleError(ValueError):
    pass


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans, cursor = [], start
    for match in _SENTENCE_END.finditer(text, start, end):
        after = match.end()
        if after >= end:
            break
        following = text[after]
        if not (following.isupper() or following.isdigit() or following in _OPENERS):
            continue
        word = _LAST_WORD.search(text, cursor, match.start())
        if word and word.group(1).lower() in ABBREVIATIONS:
            continue
        spans.append((cursor, match.start() + 1))
        cursor = after
    spans.append((cursor, end))
    return spans


def segment(text: str, *, note: bool) -> list[Line]:
    """The citable lines of a chunk's text, in order."""
    if not note:
        start, end = _trimmed(text, 0, len(text))
        return [Line(1, start, end, text[start:end])] if end > start else []
    spans: list[tuple[int, int]] = []
    position = 0
    for raw in text.split("\n"):
        start, end = _trimmed(text, position, position + len(raw))
        position += len(raw) + 1
        if start == end:
            continue
        if end - start > MAX_LINE_CHARS:
            spans += _sentence_spans(text, start, end)
        else:
            spans.append((start, end))
    return [Line(n, s, e, text[s:e]) for n, (s, e) in enumerate(spans, start=1)]


def lines_of(chunk: EvidenceChunk) -> list[Line]:
    """The citable lines of an evidence chunk, as the prompt shows them."""
    return segment(chunk.text, note=chunk.free_text)


def parse_handle(text: str) -> Handle:
    """E3.2, [E3.2], e3.2, E3.2-E3.4, E3.2–3.4, E3.2-4, or a bare item such as E5 or C1."""
    match = _HANDLE.match(text.strip())
    if match is None:
        raise HandleError(f"{text!r} is not a handle")
    item = match["item"].upper()
    first = int(match["first"]) if match["first"] else None
    last = int(match["last"]) if match["last"] else first
    if match["last"] and first is None:
        raise HandleError(f"{text!r}: a range needs a first line")
    if match["item2"]:
        other = match["item2"].upper()
        if not other[0].isalpha():
            other = item[0] + other
        if other != item:
            raise HandleError(f"{text!r}: a range must stay within one item")
    if first == 0 or (first is not None and last is not None and last < first):
        raise HandleError(f"{text!r}: lines are numbered from 1 and a range runs forward")
    return Handle(item=item, first=first, last=last)


def words(text: str) -> list[str]:
    """Words as the echo compares them: NFKC, case-folded, punctuation removed, a leading
    bullet marker ignored."""
    text = _BULLET.sub("", unicodedata.normalize("NFKC", text), count=1).casefold()
    cleaned = (
        "".join(ch for ch in word if not unicodedata.category(ch).startswith("P"))
        for word in text.split()
    )
    return [word for word in cleaned if word]


def echo_matches(echo: str, line_text: str) -> bool:
    """True if the echoed words are a prefix of the line's words and at least
    ECHO_MIN_WORDS long, or every word of the line's first row if that row has fewer."""
    given, line = words(echo), words(line_text)
    needed = min(ECHO_MIN_WORDS, len(words(line_text.split("\n")[0])))
    return needed > 0 and len(given) >= needed and line[: len(given)] == given
