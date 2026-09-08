"""Word-level diff (LCS) used to highlight a rewrite and to count changed words.

Port of WordDiff.swift. Ranges are ``(location, length)`` pairs in Python
string offsets (the Swift original uses UTF-16 offsets; the two agree for all
text a LaTeX paragraph realistically holds and are self-consistent either way).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum


class DiffKind(Enum):
    EQUAL = "equal"
    INSERTED = "inserted"
    DELETED = "deleted"


@dataclass(frozen=True)
class DiffSegment:
    text: str
    kind: DiffKind
    is_word: bool
    #: For EQUAL segments: the token as written on the old side (may differ in whitespace).
    original_text: str = ""

    def __post_init__(self) -> None:
        if not self.original_text:
            object.__setattr__(self, "original_text", self.text)


@dataclass(frozen=True)
class _Token:
    text: str
    key: str
    is_word: bool


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c in ("'", "’")


def _is_letter(c: str) -> bool:
    return unicodedata.category(c).startswith("L")


def tokenize(s: str) -> list[_Token]:
    out: list[_Token] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if _is_word_char(c):
            j = i
            while j < n and (_is_word_char(s[j])
                             or (s[j] == "-" and j + 1 < n and _is_word_char(s[j + 1]))):
                j += 1
            t = s[i:j]
            out.append(_Token(t, t, True))
            i = j
        elif c == "\\":
            j = i + 1
            while j < n and _is_letter(s[j]):
                j += 1
            if j == i + 1 and j < n:
                j += 1  # \% \, \\ etc.
            t = s[i:j]
            out.append(_Token(t, t, False))
            i = j
        elif c.isspace():
            j = i
            while j < n and s[j].isspace():
                j += 1
            out.append(_Token(s[i:j], " ", False))
            i = j
        else:
            out.append(_Token(c, c, False))
            i += 1
    return out


def diff(old: str, new: str) -> list[DiffSegment]:
    a = tokenize(old)
    b = tokenize(new)
    n, m = len(a), len(b)
    if n == 0:
        return [DiffSegment(t.text, DiffKind.INSERTED, t.is_word) for t in b]
    if m == 0:
        return [DiffSegment(t.text, DiffKind.DELETED, t.is_word) for t in a]

    # Trim the common prefix/suffix first: keeps the DP tiny for small edits.
    start = 0
    while start < n and start < m and a[start].key == b[start].key:
        start += 1
    end_a, end_b = n, m
    while end_a > start and end_b > start and a[end_a - 1].key == b[end_b - 1].key:
        end_a -= 1
        end_b -= 1

    segs: list[DiffSegment] = []
    for i in range(start):
        segs.append(DiffSegment(b[i].text, DiffKind.EQUAL, b[i].is_word, a[i].text))

    ma, mb = a[start:end_a], b[start:end_b]
    na, nb = len(ma), len(mb)
    if na or nb:
        if na * nb > 6_000_000:
            segs.extend(DiffSegment(t.text, DiffKind.DELETED, t.is_word) for t in ma)
            segs.extend(DiffSegment(t.text, DiffKind.INSERTED, t.is_word) for t in mb)
        else:
            # Weighted LCS (matching a word or punctuation mark is worth more
            # than matching a space, so words never look "moved" just to line up
            # whitespace), then backtrack.
            def weight(t: _Token) -> int:
                return 1 if t.key == " " else 3

            w = nb + 1
            table = [0] * ((na + 1) * w)
            for i in range(na - 1, -1, -1):
                row = i * w
                nxt = (i + 1) * w
                ai_key = ma[i].key
                ai_weight = weight(ma[i])
                for j in range(nb - 1, -1, -1):
                    skip = table[nxt + j]
                    right = table[row + j + 1]
                    if right > skip:
                        skip = right
                    if ai_key == mb[j].key:
                        cand = table[nxt + j + 1] + ai_weight
                        table[row + j] = cand if cand > skip else skip
                    else:
                        table[row + j] = skip
            i = j = 0
            while i < na and j < nb:
                if (ma[i].key == mb[j].key
                        and table[i * w + j] == table[(i + 1) * w + j + 1] + weight(ma[i])):
                    segs.append(DiffSegment(mb[j].text, DiffKind.EQUAL, mb[j].is_word, ma[i].text))
                    i += 1
                    j += 1
                elif table[(i + 1) * w + j] >= table[i * w + j + 1]:
                    segs.append(DiffSegment(ma[i].text, DiffKind.DELETED, ma[i].is_word))
                    i += 1
                else:
                    segs.append(DiffSegment(mb[j].text, DiffKind.INSERTED, mb[j].is_word))
                    j += 1
            while i < na:
                segs.append(DiffSegment(ma[i].text, DiffKind.DELETED, ma[i].is_word))
                i += 1
            while j < nb:
                segs.append(DiffSegment(mb[j].text, DiffKind.INSERTED, mb[j].is_word))
                j += 1

    for k in range(m - end_b):
        segs.append(DiffSegment(b[end_b + k].text, DiffKind.EQUAL,
                                b[end_b + k].is_word, a[end_a + k].text))
    return segs


def changed_word_count(segments: list[DiffSegment]) -> int:
    """A substitution counts once: max(inserted words, deleted words)."""
    ins = sum(1 for s in segments if s.kind is DiffKind.INSERTED and s.is_word)
    dele = sum(1 for s in segments if s.kind is DiffKind.DELETED and s.is_word)
    return max(ins, dele)


def highlight_ranges(segments: list[DiffSegment]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """``(old, new)`` lists of ``(location, length)`` ranges of changed tokens."""
    old_ranges: list[tuple[int, int]] = []
    new_ranges: list[tuple[int, int]] = []
    old_pos = new_pos = 0
    for s in segments:
        length = len(s.text)
        if s.kind is DiffKind.EQUAL:
            old_pos += len(s.original_text)
            new_pos += length
        elif s.kind is DiffKind.DELETED:
            old_ranges.append((old_pos, length))
            old_pos += length
        else:
            new_ranges.append((new_pos, length))
            new_pos += length
    return old_ranges, new_ranges
