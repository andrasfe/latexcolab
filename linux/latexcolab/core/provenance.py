"""Which parts of a rewrite the AI produced (port of Provenance.swift).

Ranges are ``(location, length)`` pairs in the current draft text, aligned to
WordDiff tokens (words and punctuation; whitespace is never marked). They are
carried through later edits by re-aligning old and new text token by token.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .worddiff import DiffKind, DiffSegment, diff

Range = tuple[int, int]


def _max_range(r: Range) -> int:
    return r[0] + r[1]


def _intersects(a: Range, b: Range) -> bool:
    return min(_max_range(a), _max_range(b)) > max(a[0], b[0])


def merge(ranges: list[Range]) -> list[Range]:
    """Coalesce adjacent/overlapping ranges."""
    out: list[Range] = []
    for r in sorted((r for r in ranges if r[1] > 0), key=lambda r: r[0]):
        if out and _max_range(out[-1]) >= r[0]:
            last = out[-1]
            start = min(last[0], r[0])
            end = max(_max_range(last), _max_range(r))
            out[-1] = (start, end - start)
        else:
            out.append(r)
    return out


def _covers(ranges: list[Range], r: Range) -> bool:
    if r[1] <= 0:
        return False
    return any(_intersects(x, r) for x in ranges)


def _is_markable(seg: DiffSegment) -> bool:
    return seg.text.strip() != ""


@dataclass
class Highlights:
    """Highlight sets for the two panes."""
    ai_in_draft: list[Range] = field(default_factory=list)
    user_in_draft: list[Range] = field(default_factory=list)
    ai_in_original: list[Range] = field(default_factory=list)
    user_in_original: list[Range] = field(default_factory=list)
    ai_words: int = 0
    user_words: int = 0
    ai_punctuation: int = 0


@dataclass
class Provenance:
    ai_ranges: list[Range] = field(default_factory=list)
    #: Text the AI deleted outright (no replacement), used to mark those words
    #: in the document pane.
    ai_deletions: list[str] = field(default_factory=list)

    @property
    def ranges(self) -> list[Range]:
        return list(self.ai_ranges)

    @property
    def is_empty(self) -> bool:
        return not self.ai_ranges and not self.ai_deletions

    # -- persistence -------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "aiRanges": [{"location": r[0], "length": r[1]} for r in self.ai_ranges],
            "aiDeletions": list(self.ai_deletions),
        }

    @classmethod
    def from_json(cls, obj: dict | None) -> "Provenance | None":
        if not isinstance(obj, dict):
            return None
        raw = obj.get("aiRanges") or []
        ranges: list[Range] = []
        for r in raw:
            if isinstance(r, dict) and "location" in r and "length" in r:
                ranges.append((int(r["location"]), int(r["length"])))
        deletions = [d for d in (obj.get("aiDeletions") or []) if isinstance(d, str)]
        return cls(ranges, deletions)

    # -- alignment ---------------------------------------------------------

    def remapped(self, old: str, new: str, inserted_by_ai: bool) -> "Provenance":
        """Carry AI marks from ``old`` to ``new``.

        Tokens that survive keep their mark; tokens inserted in ``new`` are
        marked when ``inserted_by_ai``. ``ai_deletions`` grows with words the AI
        removed outright.
        """
        old_ranges = self.ranges
        new_ranges: list[Range] = []
        deletions = list(self.ai_deletions)
        segs = diff(old, new)
        old_pos = new_pos = 0
        i = 0
        previous_equal_punctuation: Range | None = None

        def is_punctuation(seg: DiffSegment) -> bool:
            return _is_markable(seg) and not seg.is_word and not seg.text.startswith("\\")

        while i < len(segs):
            seg = segs[i]
            if seg.kind is DiffKind.EQUAL:
                old_len = len(seg.original_text)
                new_len = len(seg.text)
                r = (new_pos, new_len)
                if _is_markable(seg) and _covers(old_ranges, (old_pos, old_len)):
                    new_ranges.append(r)
                if is_punctuation(seg):
                    previous_equal_punctuation = r
                elif _is_markable(seg):
                    previous_equal_punctuation = None
                old_pos += old_len
                new_pos += new_len
                i += 1
                continue

            # A hunk: consecutive non-equal segments.
            j = i
            deleted_text = ""
            inserted_any = False
            inserted_text = ""
            while j < len(segs) and segs[j].kind is not DiffKind.EQUAL:
                s = segs[j]
                if s.kind is DiffKind.DELETED:
                    deleted_text += s.text
                    old_pos += len(s.text)
                else:
                    length = len(s.text)
                    if inserted_by_ai and _is_markable(s):
                        new_ranges.append((new_pos, length))
                    if _is_markable(s):
                        inserted_any = True
                    inserted_text += s.text
                    new_pos += length
                j += 1
            trimmed = deleted_text.strip()
            if inserted_by_ai and not inserted_any and trimmed:
                deletions.append(trimmed)
            # A whitespace-only fix ("method , works" → "method, works") is really
            # about the punctuation next to it: mark that mark.
            if (inserted_by_ai and not inserted_any and not trimmed
                    and (deleted_text or inserted_text)):
                if j < len(segs) and is_punctuation(segs[j]):
                    new_ranges.append((new_pos, len(segs[j].text)))
                elif previous_equal_punctuation is not None:
                    new_ranges.append(previous_equal_punctuation)
            i = j

        return Provenance(merge(new_ranges), deletions)

    # -- highlighting ------------------------------------------------------

    def highlights(self, original: str, draft: str) -> Highlights:
        """Partition the differences between document text and draft into
        AI-made and user-made, and mark AI-made words the two sides share
        (after Apply the document itself contains AI words)."""
        h = Highlights()
        ai = self.ranges
        pending_deletions = list(self.ai_deletions)
        segs = diff(original, draft)
        old_pos = new_pos = 0
        i = 0
        while i < len(segs):
            seg = segs[i]
            if seg.kind is DiffKind.EQUAL:
                old_len = len(seg.original_text)
                new_len = len(seg.text)
                r = (new_pos, new_len)
                if _is_markable(seg) and _covers(ai, r):
                    h.ai_in_draft.append(r)
                    h.ai_in_original.append((old_pos, old_len))
                    if seg.is_word:
                        h.ai_words += 1
                    else:
                        h.ai_punctuation += 1
                old_pos += old_len
                new_pos += new_len
                i += 1
                continue

            j = i
            inserted: list[tuple[Range, DiffSegment]] = []
            deleted: list[tuple[Range, DiffSegment]] = []
            while j < len(segs) and segs[j].kind is not DiffKind.EQUAL:
                s = segs[j]
                length = len(s.text)
                if s.kind is DiffKind.DELETED:
                    deleted.append(((old_pos, length), s))
                    old_pos += length
                else:
                    inserted.append(((new_pos, length), s))
                    new_pos += length
                j += 1

            if any(_is_markable(s) for _, s in inserted):
                hunk_is_ai = any(_is_markable(s) and _covers(ai, r) for r, s in inserted)
            else:
                text = "".join(s.text for _, s in deleted).strip()
                if text in pending_deletions:
                    pending_deletions.remove(text)
                    hunk_is_ai = True
                else:
                    hunk_is_ai = False

            for r, s in inserted:
                if not _is_markable(s):
                    continue
                if _covers(ai, r):
                    h.ai_in_draft.append(r)
                    if s.is_word:
                        h.ai_words += 1
                    else:
                        h.ai_punctuation += 1
                else:
                    h.user_in_draft.append(r)
                    if s.is_word:
                        h.user_words += 1
            for r, s in deleted:
                if not _is_markable(s):
                    continue
                (h.ai_in_original if hunk_is_ai else h.user_in_original).append(r)
            i = j

        h.ai_in_draft = merge(h.ai_in_draft)
        h.user_in_draft = merge(h.user_in_draft)
        h.ai_in_original = merge(h.ai_in_original)
        h.user_in_original = merge(h.user_in_original)
        return h
