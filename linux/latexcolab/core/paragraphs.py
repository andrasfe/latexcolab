"""Blank-line delimited LaTeX paragraphs (port of ParagraphLocator.swift)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParagraphRange:
    """Lines are 1-based and inclusive."""
    start_line: int
    end_line: int
    text: str

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


def lines_of(source: str) -> list[str]:
    return source.replace("\r\n", "\n").split("\n")


def _is_blank(s: str) -> bool:
    return s.strip() == ""


def paragraph_containing_line(source: str, line: int) -> ParagraphRange | None:
    """The paragraph that contains ``line``.

    When ``line`` is blank (LaTeX reports a paragraph's boxes on the blank line
    that *ends* it) the paragraph above is preferred, then the one below.
    """
    ls = lines_of(source)
    if not ls:
        return None
    idx = min(max(line, 1), len(ls)) - 1
    if _is_blank(ls[idx]):
        up = idx - 1
        while up >= 0 and _is_blank(ls[up]):
            up -= 1
        if up >= 0:
            idx = up
        else:
            down = idx + 1
            while down < len(ls) and _is_blank(ls[down]):
                down += 1
            if down >= len(ls):
                return None
            idx = down
    start = idx
    while start > 0 and not _is_blank(ls[start - 1]):
        start -= 1
    end = idx
    while end + 1 < len(ls) and not _is_blank(ls[end + 1]):
        end += 1
    return ParagraphRange(start + 1, end + 1, "\n".join(ls[start:end + 1]))


def paragraphs(source: str) -> list[ParagraphRange]:
    """Every paragraph in the source, in order."""
    ls = lines_of(source)
    out: list[ParagraphRange] = []
    i = 0
    while i < len(ls):
        if _is_blank(ls[i]):
            i += 1
            continue
        end = i
        while end + 1 < len(ls) and not _is_blank(ls[end + 1]):
            end += 1
        out.append(ParagraphRange(i + 1, end + 1, "\n".join(ls[i:end + 1])))
        i = end + 1
    return out


def replacing(rng: ParagraphRange, source: str, new_text: str) -> str:
    """Replace the given line range with ``new_text`` (which may have a different line count)."""
    ls = lines_of(source)
    if not (1 <= rng.start_line <= rng.end_line <= len(ls)):
        return source
    new_lines = new_text.replace("\r\n", "\n").split("\n")
    ls[rng.start_line - 1:rng.end_line] = new_lines
    return "\n".join(ls)


def locate(expected: str, near: ParagraphRange, source: str) -> ParagraphRange | None:
    """Re-find ``expected`` in a (possibly changed) source.

    Checks the remembered range first, then falls back to a unique (or nearest)
    exact text match.
    """
    want = expected.strip()
    ls = lines_of(source)
    if 1 <= near.start_line <= near.end_line <= len(ls):
        here = "\n".join(ls[near.start_line - 1:near.end_line])
        if here.strip() == want:
            return ParagraphRange(near.start_line, near.end_line, here)
    matches = [p for p in paragraphs(source) if p.text.strip() == want]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    return min(matches, key=lambda p: abs(p.start_line - near.start_line))
