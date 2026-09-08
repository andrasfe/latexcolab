"""Structural comparison of two LaTeX snippets (port of LaTeXStructure.swift).

Environments opened/closed, brace and math-delimiter balance, sectioning
commands. A proofread should never change any of these.
"""

from __future__ import annotations

import re

_BEGIN_RE = re.compile(r"\\begin\{([^}]*)\}")
_END_RE = re.compile(r"\\end\{([^}]*)\}")
_SECTIONING_RE = re.compile(
    r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\{")
_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")


def strip_comments(s: str) -> str:
    return _COMMENT_RE.sub("", s)


def _counts(regex: re.Pattern[str], s: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in regex.finditer(s):
        key = m.group(1) if m.re.groups >= 1 else "*"
        out[key] = out.get(key, 0) + 1
    return out


def brace_balance(s: str) -> int:
    bal = 0
    prev = " "
    for c in s:
        if c == "{" and prev != "\\":
            bal += 1
        if c == "}" and prev != "\\":
            bal -= 1
        prev = " " if (prev == "\\" and c == "\\") else c
    return bal


def dollar_count(s: str) -> int:
    n = 0
    prev = " "
    for c in s:
        if c == "$" and prev != "\\":
            n += 1
        prev = " " if (prev == "\\" and c == "\\") else c
    return n


def differences(original: str, edited: str) -> list[str]:
    """Human-readable list of structural differences (empty when identical)."""
    a, b = strip_comments(original), strip_comments(edited)
    out: list[str] = []

    def compare(label: str, ca: dict[str, int], cb: dict[str, int]) -> None:
        for key in sorted(set(ca) | set(cb)):
            x, y = ca.get(key, 0), cb.get(key, 0)
            if y > x:
                out.append(f"adds {label}{{{key}}}" + (f" ×{y - x}" if y - x > 1 else ""))
            if y < x:
                out.append(f"removes {label}{{{key}}}" + (f" ×{x - y}" if x - y > 1 else ""))

    compare("\\begin", _counts(_BEGIN_RE, a), _counts(_BEGIN_RE, b))
    compare("\\end", _counts(_END_RE, a), _counts(_END_RE, b))

    sa = len(_SECTIONING_RE.findall(a))
    sb = len(_SECTIONING_RE.findall(b))
    if sa != sb:
        out.append("adds a sectioning command" if sb > sa else "removes a sectioning command")

    ba, bb = brace_balance(a), brace_balance(b)
    if ba != bb:
        d = bb - ba
        out.append(f"leaves {d} more {{ than }} unclosed" if d > 0
                   else f"has {-d} more }} than {{")
    if dollar_count(a) % 2 != dollar_count(b) % 2:
        out.append("changes the number of $ math delimiters to an odd count")
    return out
