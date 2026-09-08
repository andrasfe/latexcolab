"""Maps PDF text onto the LaTeX source that produced it (port of SelectionMapper.swift).

Used for "edit just this sentence" and "edit exactly the words I selected".
Ranges are ``(location, length)`` pairs of Python string offsets.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

Range = tuple[int, int]


@dataclass(frozen=True)
class SourceWord:
    """A prose word in the LaTeX source with its range."""
    key: str
    range: Range


# Regions of LaTeX that produce no prose words (or words that appear elsewhere).
_SKIP_RE = re.compile("|".join([
    r"(?<!\\)%[^\n]*",
    r"\$\$[\s\S]*?\$\$", r"\$[^$\n]*\$", r"\\\[[\s\S]*?\\\]", r"\\\([\s\S]*?\\\)",
    r"\\(?:cite[a-zA-Z]*|ref|eqref|cref|Cref|autoref|pageref|vref|label|includegraphics"
    r"|url|input|include|begin|end|vspace|hspace|footnote|footnotemark|documentclass"
    r"|usepackage|bibliography|bibliographystyle|newcommand|renewcommand|setlength"
    r"|caption|centering)\*?(?:\[[^\]]*\]){0,2}(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})?",
    r"\\[a-zA-Z@]+\*?",   # remaining command names (their brace contents stay visible)
    r"\\[^a-zA-Z]",       # \% \& \_ \, \\ …
    r"[{}]",
]))

#: Letters and digits, optionally with an internal apostrophe.
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W\d_]+)?", re.UNICODE)

_LIGATURES = {
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
}


def normalize_word(word: str) -> str:
    w = word
    for k, v in _LIGATURES.items():
        w = w.replace(k, v)
    # Diacritic- and case-insensitive folding, then keep alphanumerics only.
    w = unicodedata.normalize("NFD", w)
    w = "".join(c for c in w if not unicodedata.combining(c))
    w = w.casefold()
    return "".join(c for c in w if c.isalnum())


def skip_ranges(s: str) -> list[Range]:
    """Ranges of the container that carry no prose (comments, math, commands)."""
    return [(m.start(), m.end() - m.start()) for m in _SKIP_RE.finditer(s)]


def source_words(s: str) -> list[SourceWord]:
    out: list[SourceWord] = []
    cursor = 0

    def scan(start: int, end: int) -> None:
        for m in _WORD_RE.finditer(s, start, end):
            key = normalize_word(m.group(0))
            if key:
                out.append(SourceWord(key, (m.start(), m.end() - m.start())))

    for loc, length in skip_ranges(s):
        if loc > cursor:
            scan(cursor, loc)
        cursor = max(cursor, loc + length)
    if cursor < len(s):
        scan(cursor, len(s))
    return out


def pdf_words(text: str) -> list[str]:
    """Words of text copied out of a PDF: re-join hyphenation, expand ligatures."""
    joined = (text.replace("-\n", "")
                  .replace("\u00ad", "")
                  .replace("\u2010\n", ""))
    out = []
    for m in _WORD_RE.finditer(joined):
        key = normalize_word(m.group(0))
        if key:
            out.append(key)
    return out


def _align(p: list[str], s: list[SourceWord]) -> list[int]:
    """Longest common subsequence of PDF words against source words;
    returns the matched source indices in order."""
    n, m = len(p), len(s)
    if n == 0 or m == 0 or n * m > 4_000_000:
        return []
    w = m + 1
    table = [0] * ((n + 1) * w)
    keys = [x.key for x in s]
    for i in range(n - 1, -1, -1):
        row = i * w
        nxt = (i + 1) * w
        pi = p[i]
        for j in range(m - 1, -1, -1):
            if pi == keys[j]:
                table[row + j] = table[nxt + j + 1] + 1
            else:
                a = table[nxt + j]
                b = table[row + j + 1]
                table[row + j] = a if a > b else b
    i = j = 0
    matched: list[int] = []
    while i < n and j < m:
        if p[i] == keys[j]:
            matched.append(j)
            i += 1
            j += 1
        elif table[(i + 1) * w + j] >= table[i * w + j + 1]:
            i += 1
        else:
            j += 1
    return matched


def _tidy(rng: Range, s: str, pdf_text: str | None) -> Range:
    """Grow ``rng`` so braces are balanced (opening ``\\cmd{`` before it, closing
    ``}`` after it) and trailing punctuation the PDF text also ends with is included."""
    start = rng[0]
    end = rng[0] + rng[1]
    n = len(s)

    if pdf_text:
        stripped = pdf_text.strip()
        last = stripped[-1] if stripped else ""
        if last in ".,;:!?":
            k = end
            while k < n and s[k] in "}')”":
                k += 1
            if k < n and s[k] == last:
                end = k + 1

    def balance() -> int:
        bal = 0
        i = start
        while i < end:
            c = s[i]
            prev = s[i - 1] if i > 0 else " "
            if c == "{" and prev != "\\":
                bal += 1
            if c == "}" and prev != "\\":
                bal -= 1
            i += 1
        return bal

    guard = 0
    while balance() < 0 and start > 0 and guard < 20:
        # Extend backwards to the opening brace and its command.
        i = start - 1
        depth = 0
        while i >= 0:
            c = s[i]
            if c == "}":
                depth += 1
            if c == "{":
                if depth == 0:
                    break
                depth -= 1
            i -= 1
        if i < 0:
            break
        j = i - 1
        while j >= 0 and (s[j].isalpha() or s[j] == "*"):
            j -= 1
        start = j if (j >= 0 and s[j] == "\\") else i
        guard += 1

    guard = 0
    while balance() > 0 and end < n and guard < 20:
        i = end
        depth = 0
        while i < n:
            c = s[i]
            if c == "{":
                depth += 1
            if c == "}":
                if depth == 0:
                    break
                depth -= 1
            i += 1
        if i >= n:
            break
        end = i + 1
        guard += 1

    return start, end - start


def source_range_for_pdf_text(pdf_text: str, container: str) -> Range | None:
    """The substring of ``container`` that produced ``pdf_text``.

    Needs at least 60 % of the PDF words to be found, in order.
    """
    p = pdf_words(pdf_text)
    s = source_words(container)
    if not p or not s:
        return None
    matched = _align(p, s)
    if len(matched) < 0.6 * len(p) or not matched:
        return None
    first, last = matched[0], matched[-1]
    start = s[first].range[0]
    end = s[last].range[0] + s[last].range[1]
    return _tidy((start, end - start), container, pdf_text)


# --------------------------------------------------------------------------
# Sentences
# --------------------------------------------------------------------------

_STRUCTURAL_LINE_RE = re.compile(
    r"^\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph|begin|end"
    r"|item|label|caption|centering|vspace|hspace|includegraphics|input|include|maketitle"
    r"|title|author|date|newpage|clearpage)\b")

ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "cf", "fig", "figs", "eq", "eqs", "sec", "secs", "al", "no", "nos",
    "dr", "prof", "mr", "mrs", "ms", "st", "approx", "ref", "refs", "resp", "viz", "ca", "ch",
    "tab", "alg", "vol", "pp", "p",
}


def terminators(s: str) -> list[int]:
    """Sentence terminators (outside comments/math/commands) as offsets of the
    terminator character."""
    skips = skip_ranges(s)

    def skipped(i: int) -> bool:
        return any(loc <= i < loc + length for loc, length in skips)

    out: list[int] = []
    n = len(s)
    i = 0
    while i < n:
        c = s[i]
        if c in ".!?" and not skipped(i):
            # Must be followed by whitespace/end, possibly after closers.
            k = i + 1
            while k < n and s[k] in "}')”\"":
                k += 1
            followed_by_break = k >= n or s[k].isspace()
            ok = followed_by_break
            if ok and c == ".":
                # Abbreviations, initials, decimals ("3.5" has no break so already excluded).
                j = i - 1
                while j >= 0 and (s[j].isalpha() or s[j] == "."):
                    j -= 1
                word = s[j + 1:i].lower()
                bare = word[:-1] if word.endswith(".") else word
                if bare in ABBREVIATIONS or word in ABBREVIATIONS:
                    ok = False
                if len(word) == 1 and word.isalpha() and j >= 0 and s[j] == " ":
                    ok = False  # "A. Smith"
                # The next sentence should start with an uppercase letter, digit,
                # \ or ( — a lowercase letter suggests an abbreviation.
                m = k
                while m < n and s[m].isspace():
                    m += 1
                if m < n and s[m].islower() and s[m].isalpha():
                    ok = False
            if ok:
                out.append(k - 1)   # include closers in the sentence
        i += 1
    return out


def _line_ranges(s: str) -> list[Range]:
    """Every line of ``s`` as ``(location, length)``, the terminator included."""
    out: list[Range] = []
    start = 0
    n = len(s)
    while start <= n:
        nl = s.find("\n", start)
        if nl < 0:
            out.append((start, n - start))
            break
        out.append((start, nl - start + 1))
        start = nl + 1
        if start > n:
            break
    return out


def sentence_range(container: str, offset: int) -> Range:
    """The sentence of ``container`` containing ``offset``.

    Sentences also break at blank lines, at lines without prose and at lines
    that start with a structural command.
    """
    n = len(container)
    ends = terminators(container)
    start = 0
    for e in ends:
        if e < offset:
            start = e + 1
    end = n
    for e in ends:
        if e >= offset:
            end = e + 1
            break

    words = source_words(container)
    for loc, length in _line_ranges(container):
        line_end = loc + length
        content = container[loc:line_end].strip()
        has_prose = any(loc <= w.range[0] < line_end for w in words)
        structural = _STRUCTURAL_LINE_RE.search(content) is not None
        if not content or not has_prose or structural:
            if line_end <= offset:
                start = max(start, line_end)
            elif loc > offset:
                end = min(end, loc)
            elif content and loc <= offset < line_end:
                # Offset is on a heading line itself: the "sentence" is that line.
                start = loc
                end = line_end

    while start < end and container[start].isspace():
        start += 1
    while end > start and container[end - 1].isspace():
        end -= 1
    return _tidy((start, end - start), container, None)


def sentence_range_for_pdf_word(container: str, word: str, near_offset: int) -> Range | None:
    """Sentence containing a word seen in the PDF; when the word occurs more than
    once, the occurrence nearest ``near_offset`` wins."""
    key = normalize_word(word)
    if not key:
        return None
    candidates = [w for w in source_words(container) if w.key == key]
    if not candidates:
        return None
    best = min(candidates, key=lambda w: abs(w.range[0] - near_offset))
    return sentence_range(container, best.range[0])


def offset_of_line(line: int, container: str) -> int:
    """Offset of the start of ``line`` (1-based) within ``container``."""
    current = 1
    i = 0
    n = len(container)
    while i < n and current < line:
        if container[i] == "\n":
            current += 1
        i += 1
    return i
