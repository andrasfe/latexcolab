"""Semantic comparison of two versions of a paragraph (port of ParagraphCompare.swift).

Two independent halves: the model's verdict (``compare``), and instant
deterministic integrity checks that need no model (``integrity``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .lmstudio import (EmptyResponse, ExhaustedThinking, LMStudioService, NoModel,
                       _THINK_RE)

VERDICT_SAME = "same"
VERDICT_MINOR = "minor"
VERDICT_CHANGED = "changed"
VERDICT_UNKNOWN = "unknown"

_HEADLINES = {
    VERDICT_SAME: "Says the same thing",
    VERDICT_MINOR: "Same facts, slight shift in tone or emphasis",
    VERDICT_CHANGED: "Meaning changed",
    VERDICT_UNKNOWN: "Could not interpret the model's answer",
}


@dataclass
class SemanticComparison:
    verdict: str
    summary: str
    changes: list[str] = field(default_factory=list)
    meaning_differences: list[str] = field(default_factory=list)
    latex_issues: list[str] = field(default_factory=list)
    raw: str = ""
    model: str = ""

    @property
    def headline(self) -> str:
        return _HEADLINES.get(self.verdict, _HEADLINES[VERDICT_UNKNOWN])


#: Result for two textually identical sides — no model call needed.
def identical_comparison() -> SemanticComparison:
    return SemanticComparison(VERDICT_SAME,
                              "Both sides are identical; there is nothing to compare.",
                              model="none")


@dataclass
class CompareRequest:
    original: str
    edited: str
    model: str
    temperature: float = 0.1


COMPARE_SYSTEM_PROMPT = """\
You are a careful technical editor comparing two versions of one LaTeX paragraph from an academic paper: ORIGINAL and EDITED. Judge meaning, not style. Ignore whitespace and line-break differences.

Reply with one JSON object and nothing else — no prose before or after, no code fences — using exactly these keys:
{
  "verdict": "same" | "minor" | "changed",
  "summary": "one sentence answering whether the edited version says the same thing as the original",
  "changes": ["each concrete edit in plain language, quoting the words that changed"],
  "meaning_differences": ["claims, numbers, conditions, causal links, hedges, citations or references that were added, removed, strengthened or weakened; empty list if none"],
  "latex_issues": ["LaTeX commands, math, citations or references that were broken, dropped or altered; empty list if none"]
}

Verdict rules:
- "same": every claim, quantity, condition, hedge and reference is preserved; only wording, grammar or punctuation changed.
- "minor": emphasis, tone or hedging shifted slightly, but no fact was added or removed.
- "changed": a claim, number, condition, causal link, citation or reference was added, removed or altered."""


def compare_user_prompt(req: CompareRequest) -> str:
    return (f"ORIGINAL:\n<original>\n{req.original}\n</original>\n\n"
            f"EDITED:\n<edited>\n{req.edited}\n</edited>\n\n"
            "Compare them and reply with the JSON object only.")


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                t = item.strip()
                if t:
                    out.append(t)
            elif isinstance(item, dict):
                joined = " — ".join(v for v in item.values() if isinstance(v, str))
                if joined:
                    out.append(joined)
        return out
    if isinstance(value, str):
        t = value.strip()
        return [] if not t or t.lower() == "none" else [t]
    return []


def parse_comparison(raw: str, model: str) -> SemanticComparison:
    """Lenient parser: strips reasoning/fences, takes the outermost ``{...}`` block,
    accepts strings where lists were expected. Falls back to the raw text."""
    text = _THINK_RE.sub("", raw)
    text = text.replace("```json", "```").replace("```", "").strip()

    open_i = text.find("{")
    close_i = text.rfind("}")
    if 0 <= open_i < close_i:
        try:
            obj = json.loads(text[open_i:close_i + 1])
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            v = str(obj.get("verdict") or "").lower()
            if "same" in v or "identical" in v or "preserv" in v:
                verdict = VERDICT_SAME
            elif "minor" in v or "slight" in v:
                verdict = VERDICT_MINOR
            elif "chang" in v or "differ" in v:
                verdict = VERDICT_CHANGED
            else:
                verdict = VERDICT_UNKNOWN
            summary = str(obj.get("summary") or "").strip()
            if not summary:
                summary = "The model did not give a verdict." if verdict == VERDICT_UNKNOWN else ""
            return SemanticComparison(
                verdict=verdict,
                summary=summary,
                changes=_as_list(obj.get("changes")),
                meaning_differences=_as_list(
                    obj.get("meaning_differences", obj.get("meaningDifferences"))),
                latex_issues=_as_list(obj.get("latex_issues", obj.get("latexIssues"))),
                raw=raw, model=model)

    short = text[:600] + "…" if len(text) > 600 else text
    return SemanticComparison(VERDICT_UNKNOWN, short, raw=raw, model=model)


def compare(service: LMStudioService, req: CompareRequest) -> SemanticComparison:
    if not req.model:
        raise NoModel()
    body = {
        "model": req.model,
        "messages": [
            {"role": "system", "content": COMPARE_SYSTEM_PROMPT},
            {"role": "user", "content": compare_user_prompt(req)},
        ],
        "temperature": req.temperature,
        "max_tokens": 8192,
        "stream": False,
    }
    obj = service.send_chat(body)
    choices = obj.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        text = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    if not text.strip():
        if message.get("reasoning_content"):
            raise ExhaustedThinking()
        raise EmptyResponse()
    return parse_comparison(text, obj.get("model") or req.model)


# --------------------------------------------------------------------------
# Deterministic checks
# --------------------------------------------------------------------------

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass(frozen=True)
class Issue:
    text: str
    severity: str


_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}")
_REF_RE = re.compile(r"\\(?:ref|eqref|cref|Cref|autoref|pageref|vref|nameref)\*?\{([^}]*)\}")
_LABEL_RE = re.compile(r"\\label\{([^}]*)\}")
_MATH_RE = re.compile(r"\$\$[\s\S]+?\$\$|\$[^$\n]+\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]")
_NUMBER_RE = re.compile(r"(?<![\w\\{])[-+]?\d[\d,]*(?:\.\d+)?%?")
_ENV_RE = re.compile(r"\\begin\{([^}]*)\}")
_WORD_RE = re.compile(r"[A-Za-z']+")
_COMMAND_RE = re.compile(r"\\[a-zA-Z@]+\*?")
_ABBREV_RE = re.compile(r"\b(?:e\.g|i\.e|etc|vs|cf|Fig|Eq|Sec|al)\.")
_DECIMAL_RE = re.compile(r"\d\.\d")

NEGATIONS = {"not", "no", "never", "cannot", "can't", "won't", "don't", "doesn't", "didn't",
             "isn't", "aren't", "wasn't", "weren't", "without", "neither", "nor", "none",
             "nothing"}
HEDGES = {"may", "might", "could", "likely", "unlikely", "possibly", "probably", "suggests",
          "suggest", "appears", "appear", "seems", "seem", "approximately", "roughly", "about",
          "typically", "often", "sometimes", "generally", "usually", "potentially", "arguably"}
INTENSIFIERS = {"always", "all", "every", "never", "guarantees", "guarantee", "proves", "prove",
                "significantly", "substantially", "clearly", "certainly", "definitely", "must",
                "will", "only"}


def _matches(regex: re.Pattern[str], s: str, group: int = 1) -> list[str]:
    """Group ``group`` when the pattern has it and it matched, else the whole match."""
    out = []
    for m in regex.finditer(s):
        text = m.group(group) if (group <= regex.groups and m.group(group) is not None) \
            else m.group(0)
        out.append(text.strip())
    return out


def _keys(regex: re.Pattern[str], s: str) -> list[str]:
    out: list[str] = []
    for raw in _matches(regex, s):
        for part in raw.split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out


def _counted(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def _words(s: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(s)]


def sentence_count(s: str) -> int:
    # Strip LaTeX commands and math first so "Fig.~\ref{x}" or "3.5" don't count.
    t = s
    for regex in (_MATH_RE, _CITE_RE, _REF_RE, _LABEL_RE):
        t = regex.sub(" X ", t)
    t = _COMMAND_RE.sub(" ", t)
    t = _ABBREV_RE.sub("X", t)
    t = _DECIMAL_RE.sub("X", t)
    parts = [p for p in re.split(r"[.!?]", t) if p.strip()]
    stripped = t.strip()
    ends_with_terminator = bool(stripped) and stripped[-1] in ".!?"
    if ends_with_terminator:
        return max(len(parts), 0)
    return max(len(parts) - 1, 0) + 1


def integrity(original: str, edited: str) -> list[Issue]:
    """Catches the most common ways an edit silently changes meaning."""
    issues: list[Issue] = []

    def diff_sets(label: str, a: list[str], b: list[str], severity: str) -> None:
        sa, sb = set(a), set(b)
        for k in sorted(sa - sb):
            issues.append(Issue(f"{label} `{k}` was removed", severity))
        for k in sorted(sb - sa):
            issues.append(Issue(f"{label} `{k}` was added", severity))

    diff_sets("Citation", _keys(_CITE_RE, original), _keys(_CITE_RE, edited), SEVERITY_ERROR)
    diff_sets("Reference", _keys(_REF_RE, original), _keys(_REF_RE, edited), SEVERITY_ERROR)
    diff_sets("Label", _keys(_LABEL_RE, original), _keys(_LABEL_RE, edited), SEVERITY_ERROR)
    diff_sets("Environment", _matches(_ENV_RE, original), _matches(_ENV_RE, edited), SEVERITY_ERROR)

    # Math and numbers are compared as multisets so a repeated value counts.
    math_a = _counted([m.replace(" ", "") for m in _matches(_MATH_RE, original, 0)])
    math_b = _counted([m.replace(" ", "") for m in _matches(_MATH_RE, edited, 0)])
    for m, n in sorted(math_a.items()):
        if math_b.get(m, 0) < n:
            issues.append(Issue(f"Math {m} was removed or altered", SEVERITY_ERROR))
    for m, n in sorted(math_b.items()):
        if math_a.get(m, 0) < n:
            issues.append(Issue(f"Math {m} was added or altered", SEVERITY_ERROR))

    # Numbers inside math and command arguments are covered by the checks above.
    def prose(s: str) -> str:
        t = s
        for regex in (_MATH_RE, _CITE_RE, _REF_RE, _LABEL_RE):
            t = regex.sub(" ", t)
        return t

    num_a = _counted(_matches(_NUMBER_RE, prose(original), 0))
    num_b = _counted(_matches(_NUMBER_RE, prose(edited), 0))
    removed = sorted(k for k, v in num_a.items() if num_b.get(k, 0) < v)
    added = sorted(k for k, v in num_b.items() if num_a.get(k, 0) < v)
    if len(removed) == 1 and len(added) == 1:
        issues.append(Issue(f"Number {removed[0]} became {added[0]}", SEVERITY_ERROR))
    else:
        for n in removed:
            issues.append(Issue(f"Number {n} is missing from the edit", SEVERITY_ERROR))
        for n in added:
            issues.append(Issue(f"Number {n} appears only in the edit", SEVERITY_ERROR))

    wa, wb = _words(original), _words(edited)

    def count_words(vocab: set[str], ws: list[str]) -> int:
        return sum(1 for w in ws if w in vocab)

    neg_a, neg_b = count_words(NEGATIONS, wa), count_words(NEGATIONS, wb)
    if neg_a != neg_b:
        issues.append(Issue(
            f"Negation words changed: {neg_a} → {neg_b} (check the claim was not inverted)",
            SEVERITY_WARNING))
    hedge_a, hedge_b = count_words(HEDGES, wa), count_words(HEDGES, wb)
    if hedge_a != hedge_b:
        tail = "claims sound more certain" if hedge_b < hedge_a else "claims sound more tentative"
        issues.append(Issue(f"Hedging words changed: {hedge_a} → {hedge_b} ({tail})",
                            SEVERITY_WARNING))
    int_a, int_b = count_words(INTENSIFIERS, wa), count_words(INTENSIFIERS, wb)
    if int_a != int_b:
        issues.append(Issue(f"Absolute/intensifying words changed: {int_a} → {int_b}",
                            SEVERITY_WARNING))

    sent_a, sent_b = sentence_count(original), sentence_count(edited)
    if sent_a != sent_b:
        issues.append(Issue(f"Sentence count changed: {sent_a} → {sent_b}", SEVERITY_INFO))
    if wa:
        ratio = len(wb) / len(wa)
        if ratio < 0.7:
            issues.append(Issue(
                f"Edit is {int((1 - ratio) * 100)}% shorter — content may have been dropped",
                SEVERITY_WARNING))
        if ratio > 1.4:
            issues.append(Issue(
                f"Edit is {int((ratio - 1) * 100)}% longer — content may have been added",
                SEVERITY_WARNING))
    return issues
