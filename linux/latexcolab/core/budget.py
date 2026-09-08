"""Enforces "correct the syntax, but change at most N words" (port of EditBudget.swift).

The AI's reply is diffed against the author's text; punctuation, LaTeX and
typo-level fixes are always kept, rewordings are kept smallest-first until the
word budget is spent, and everything else is reverted to the author's words.
"""

from __future__ import annotations

from dataclasses import dataclass

from .worddiff import DiffKind, DiffSegment, diff

#: Commands whose addition or removal changes document structure or content
#: rather than fixing syntax.
STRUCTURAL_COMMANDS = {
    "\\begin", "\\end", "\\part", "\\chapter", "\\section", "\\subsection", "\\subsubsection",
    "\\paragraph", "\\subparagraph", "\\item", "\\label", "\\input", "\\include", "\\caption",
    "\\documentclass", "\\usepackage", "\\newcommand", "\\renewcommand", "\\bibliography",
    "\\bibliographystyle", "\\maketitle", "\\appendix", "\\newpage", "\\clearpage", "\\footnote",
    "\\cite", "\\citep", "\\citet", "\\ref", "\\eqref", "\\cref", "\\Cref", "\\autoref",
}

#: Words whose "typo-level" variants flip meaning; never free.
GUARDED = {"not", "no", "nor", "now", "never", "none", "non", "un"}


@dataclass(frozen=True)
class EditSuggestion:
    """One contiguous change between the author's text and the AI's reply."""
    id: int
    from_text: str
    to_text: str
    #: Words this change costs against the budget (0 = free fix).
    cost: int
    #: Adds or removes LaTeX structure; never applied automatically.
    is_structural: bool = False

    @property
    def is_free(self) -> bool:
        return self.cost == 0 and not self.is_structural

    @property
    def plain_label(self) -> str:
        f = self.from_text.strip()
        t = self.to_text.strip()
        if not f and not t:
            return "remove extra space" if len(self.from_text) > len(self.to_text) else "insert space"
        if not f:
            return f"insert “{t}”"
        if not t:
            return f"delete “{f}”"
        return f"“{f}” → “{t}”"

    @property
    def label(self) -> str:
        return ("structural change: " if self.is_structural else "") + self.plain_label


@dataclass(frozen=True)
class EditBudgetResult:
    text: str
    applied: list[EditSuggestion]
    dropped: list[EditSuggestion]

    @property
    def words_used(self) -> int:
        return sum(s.cost for s in self.applied)

    @property
    def free_fixes(self) -> int:
        return sum(1 for s in self.applied if s.is_free)

    @property
    def word_changes_applied(self) -> int:
        return sum(1 for s in self.applied if not s.is_free)


@dataclass(frozen=True)
class EditMeasure:
    word_changes: int
    free_fixes: int
    rewordings: int


def levenshtein(a: str, b: str) -> int:
    s, t = a, b
    if not s:
        return len(t)
    if not t:
        return len(s)
    prev = list(range(len(t) + 1))
    for i in range(1, len(s) + 1):
        cur = [i] + [0] * len(t)
        si = s[i - 1]
        for j in range(1, len(t) + 1):
            sub = prev[j - 1] + (0 if si == t[j - 1] else 1)
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, sub)
        prev = cur
    return prev[len(t)]


def is_minor_variant(a: str, b: str) -> bool:
    """Typo, capitalization or inflection fix (show/shows, recieve/receive, a/an)."""
    x, y = a.lower(), b.lower()
    if x == y:
        return True
    if x in GUARDED or y in GUARDED:
        return False
    d = levenshtein(x, y)
    if d <= 1:
        return True
    return d == 2 and min(len(x), len(y)) >= 5


def _is_whitespace(seg: DiffSegment) -> bool:
    return seg.text.strip() == ""


def _is_command(t: str) -> bool:
    return len(t) > 1 and t.startswith("\\") and all(c.isalpha() for c in t[1:])


@dataclass(frozen=True)
class _Hunk:
    start: int
    end: int
    deleted_words: list[str]
    inserted_words: list[str]
    deleted_commands: list[str]
    inserted_commands: list[str]
    from_text: str
    to_text: str

    @property
    def is_structural(self) -> bool:
        return (any(c in STRUCTURAL_COMMANDS for c in self.deleted_commands)
                or any(c in STRUCTURAL_COMMANDS for c in self.inserted_commands))

    @property
    def cost(self) -> int:
        if not self.deleted_words and not self.inserted_words:
            return 0
        if (len(self.deleted_words) == len(self.inserted_words)
                and all(is_minor_variant(a, b)
                        for a, b in zip(self.deleted_words, self.inserted_words))):
            return 0
        return max(len(self.deleted_words), len(self.inserted_words))


def _hunks(segs: list[DiffSegment]) -> list[_Hunk]:
    """Runs of changed tokens.

    Runs separated only by whitespace are merged so a phrase replacement
    ("very big" → "huge") is accepted or rejected whole.
    """
    out: list[_Hunk] = []
    i = 0
    n = len(segs)
    while i < n:
        if segs[i].kind is DiffKind.EQUAL:
            i += 1
            continue
        j = i
        while j < n:
            if segs[j].kind is not DiffKind.EQUAL:
                j += 1
                continue
            # Look past equal whitespace to see if the change continues.
            k = j
            while k < n and segs[k].kind is DiffKind.EQUAL and _is_whitespace(segs[k]):
                k += 1
            if k < n and segs[k].kind is not DiffKind.EQUAL and k > j:
                j = k
            else:
                break
        chunk = segs[i:j]
        out.append(_Hunk(
            start=i,
            end=j,
            deleted_words=[s.text for s in chunk if s.kind is DiffKind.DELETED and s.is_word],
            inserted_words=[s.text for s in chunk if s.kind is DiffKind.INSERTED and s.is_word],
            deleted_commands=[s.text for s in chunk
                              if s.kind is DiffKind.DELETED and _is_command(s.text)],
            inserted_commands=[s.text for s in chunk
                               if s.kind is DiffKind.INSERTED and _is_command(s.text)],
            from_text="".join(s.original_text for s in chunk if s.kind is not DiffKind.INSERTED),
            to_text="".join(s.text for s in chunk if s.kind is not DiffKind.DELETED),
        ))
        i = j
    return out


def measure(original: str, new: str) -> EditMeasure:
    """How far ``new`` departs from ``original`` under the budget's accounting."""
    hs = _hunks(diff(original, new))
    paid = [h for h in hs if h.cost > 0 or h.is_structural]
    return EditMeasure(
        word_changes=sum(max(h.cost, 1 if h.is_structural else 0) for h in paid),
        free_fixes=len(hs) - len(paid),
        rewordings=len(paid),
    )


def constrain(original: str, rewrite: str, max_words: int) -> EditBudgetResult:
    segs = diff(original, rewrite)
    hs = _hunks(segs)
    accepted: set[int] = set()
    used = 0
    # Structural changes are never applied automatically, whatever the budget.
    for i, h in enumerate(hs):
        if h.cost == 0 and not h.is_structural:
            accepted.add(i)
    paid = sorted(((i, h) for i, h in enumerate(hs) if h.cost > 0 and not h.is_structural),
                  key=lambda pair: (pair[1].cost, pair[0]))
    for i, h in paid:
        if used + h.cost <= max_words:
            accepted.add(i)
            used += h.cost

    parts: list[str] = []
    hunk_index = 0
    seg_index = 0
    while seg_index < len(segs):
        if hunk_index < len(hs) and hs[hunk_index].start == seg_index:
            h = hs[hunk_index]
            keep = hunk_index in accepted
            for k in range(h.start, h.end):
                seg = segs[k]
                if seg.kind is DiffKind.EQUAL:
                    parts.append(seg.text if keep else seg.original_text)
                elif seg.kind is DiffKind.INSERTED:
                    if keep:
                        parts.append(seg.text)
                else:
                    if not keep:
                        parts.append(seg.text)
            seg_index = h.end
            hunk_index += 1
        else:
            parts.append(segs[seg_index].original_text)
            seg_index += 1

    def suggestion(i: int, h: _Hunk) -> EditSuggestion:
        return EditSuggestion(i, h.from_text, h.to_text,
                              max(h.cost, 1 if h.is_structural else 0), h.is_structural)

    applied = [suggestion(i, h) for i, h in enumerate(hs) if i in accepted]
    dropped = [suggestion(i, h) for i, h in enumerate(hs) if i not in accepted]
    return EditBudgetResult("".join(parts), applied, dropped)


def apply_suggestion(s: EditSuggestion, text: str) -> str | None:
    """Apply one previously dropped suggestion by hand (first exact occurrence)."""
    if not s.from_text:
        return None
    idx = text.find(s.from_text)
    if idx < 0:
        return None
    return text[:idx] + s.to_text + text[idx + len(s.from_text):]
