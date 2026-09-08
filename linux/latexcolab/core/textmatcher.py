"""Fallback PDF→source mapping by text similarity (port of TextMatcher.swift).

Used when the project has no SyncTeX data: the text near the click is matched
against a de-TeXed version of every paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .paragraphs import ParagraphRange, paragraphs

_PATTERNS = [
    r"(?<!\\)%[^\n]*",                                          # comments
    r"\\(?:label|cite[a-zA-Z]*|ref|eqref|autoref|cref|Cref|includegraphics|input|include"
    r"|bibliography|bibliographystyle|usepackage|documentclass)\*?(?:\[[^\]]*\])?\{[^}]*\}",
    r"\\(?:begin|end)\{[^}]*\}",
    r"\$\$[\s\S]*?\$\$",
    r"\\\[[\s\S]*?\\\]",
    r"\\\([\s\S]*?\\\)",
    r"\$[^$\n]*\$",
    r"\\[a-zA-Z@]+\*?",                                         # remaining commands
]

_COMPILED = [re.compile(p) for p in _PATTERNS]

#: Everything that is not a Unicode alphanumeric, matching Swift's
#: ``CharacterSet.alphanumerics.inverted``.
_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Match:
    file: str
    paragraph: ParagraphRange
    score: float


def tokens_plain(plain: str) -> list[str]:
    return [t for t in _NON_ALNUM.split(plain.lower()) if len(t) >= 2]


def tokens_latex(latex: str) -> list[str]:
    """Lower-cased word tokens with LaTeX markup stripped."""
    s = latex
    for regex in _COMPILED:
        s = regex.sub(" ", s)
    return tokens_plain(s)


def tokens_pdf(pdf_text: str) -> list[str]:
    """Tokens for text extracted from a PDF (re-joins hyphenated line breaks)."""
    joined = pdf_text.replace("-\n", "").replace("\u00ad", "")
    return tokens_plain(joined)


def _bigrams(t: list[str]) -> set[str]:
    if len(t) < 2:
        return set()
    return {t[i] + " " + t[i + 1] for i in range(len(t) - 1)}


def score(query: list[str], candidate: list[str]) -> float:
    """0…1: how much of ``query`` is covered by ``candidate``."""
    if not query or not candidate:
        return 0.0
    cset = set(candidate)
    uni = sum(1 for q in query if q in cset) / len(query)
    qb = _bigrams(query)
    cb = _bigrams(candidate)
    bi = uni if not qb else len(qb & cb) / len(qb)
    return 0.4 * uni + 0.6 * bi


def best_match(pdf_text: str, sources: list[tuple[str, str]],
               threshold: float = 0.45) -> Match | None:
    """Best paragraph across ``sources`` (``(path, latex source)``) for the PDF text."""
    q = tokens_pdf(pdf_text)
    if len(q) < 3:
        return None
    best: Match | None = None
    for path, source in sources:
        for para in paragraphs(source):
            c = tokens_latex(para.text)
            if len(c) < 3:
                continue
            s = score(q, c)
            if s > (best.score if best else threshold) or (best is None and s >= threshold):
                best = Match(path, para, s)
    return best
