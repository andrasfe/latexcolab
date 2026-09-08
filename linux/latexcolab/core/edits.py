"""Per-project rewrite store, ``<project>/latexcolab-edits.json`` (port of EditsStore.swift).

The JSON shape is byte-compatible with the macOS app, so a project folder can
be moved between the two.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import textmatcher
from .provenance import Provenance

FILE_NAME = "latexcolab-edits.json"


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(value, fallback: datetime | None = None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return fallback
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def normalize(s: str) -> str:
    return s.replace("\r\n", "\n").strip()


@dataclass
class ParagraphVersion:
    """A paragraph text that was in the document until ``replaced_at``."""
    text: str
    replaced_at: datetime = field(default_factory=now)
    note: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())

    def to_json(self) -> dict:
        obj = {"id": self.id, "text": self.text, "replacedAt": _iso(self.replaced_at)}
        if self.note is not None:
            obj["note"] = self.note
        return obj

    @classmethod
    def from_json(cls, obj: dict) -> "ParagraphVersion":
        return cls(
            text=obj.get("text", ""),
            replaced_at=_parse_date(obj.get("replacedAt"), now()) or now(),
            note=obj.get("note"),
            id=obj.get("id") or str(uuid.uuid4()).upper(),
        )


@dataclass
class ParagraphEdit:
    """One paragraph rewrite.

    ``original`` is the paragraph as it stands in the document (updated on
    Apply); ``draft`` is the rewrite. When they are equal the paragraph is "in
    sync". ``history`` holds every earlier text that Apply replaced, oldest
    first, so any version can be restored.
    """
    file: str
    original: str
    draft: str
    line_hint: int = 0
    max_words: int = 20
    instructions: str = ""
    applied: bool = False
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)
    applied_at: datetime | None = None
    history: list[ParagraphVersion] = field(default_factory=list)
    #: True when ``original`` is a sentence/selection inside a paragraph.
    partial: bool = False
    #: Which words/punctuation of ``draft`` the AI produced.
    provenance: Provenance | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())

    @property
    def is_in_sync(self) -> bool:
        return normalize(self.original) == normalize(self.draft)

    def mark_applied(self, at: datetime | None = None, note: str | None = None) -> None:
        """Record that ``draft`` replaced ``original`` in the document."""
        stamp = at or now()
        self.history.append(ParagraphVersion(self.original, stamp, note))
        self.original = self.draft
        self.applied = True
        self.applied_at = stamp
        self.updated_at = stamp

    def to_json(self) -> dict:
        obj: dict = {
            "id": self.id,
            "file": self.file,
            "original": self.original,
            "draft": self.draft,
            "applied": self.applied,
            "lineHint": self.line_hint,
            "maxWords": self.max_words,
            "instructions": self.instructions,
            "createdAt": _iso(self.created_at),
            "updatedAt": _iso(self.updated_at),
            "history": [v.to_json() for v in self.history],
            "partial": self.partial,
        }
        if self.applied_at is not None:
            obj["appliedAt"] = _iso(self.applied_at)
        if self.provenance is not None and not self.provenance.is_empty:
            obj["provenance"] = self.provenance.to_json()
        return obj

    @classmethod
    def from_json(cls, obj: dict) -> "ParagraphEdit":
        created = _parse_date(obj.get("createdAt"), now()) or now()
        return cls(
            id=obj.get("id") or str(uuid.uuid4()).upper(),
            file=obj.get("file", ""),
            original=obj.get("original", ""),
            draft=obj.get("draft", ""),
            applied=bool(obj.get("applied", False)),
            line_hint=int(obj.get("lineHint", 0) or 0),
            max_words=int(obj.get("maxWords", 20) or 0),
            instructions=obj.get("instructions", "") or "",
            created_at=created,
            updated_at=_parse_date(obj.get("updatedAt"), created) or created,
            applied_at=_parse_date(obj.get("appliedAt"), None),
            history=[ParagraphVersion.from_json(v) for v in (obj.get("history") or [])],
            partial=bool(obj.get("partial", False)),
            provenance=Provenance.from_json(obj.get("provenance")),
        )


@dataclass(frozen=True)
class Match:
    edit: ParagraphEdit
    #: False when the document paragraph no longer equals the record's
    #: ``original`` (edited by hand or reverted outside the app).
    exact: bool


class EditsStore:
    def __init__(self, project_path: str | os.PathLike):
        self.path = os.path.join(str(project_path), FILE_NAME)
        self.edits: list[ParagraphEdit] = []

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.edits = []
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        self.edits = [ParagraphEdit.from_json(e) for e in (doc.get("edits") or [])]

    def save(self) -> None:
        doc = {"version": 1, "edits": [e.to_json() for e in self.edits]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            # Two-space indent with spaces around ":" mirrors Foundation's
            # .prettyPrinted output, so the file diffs cleanly across platforms.
            json.dump(doc, fh, indent=2, sort_keys=True, separators=(",", " : "))
            fh.write("\n")
        os.replace(tmp, self.path)

    # -- lookup ------------------------------------------------------------

    def find(self, file: str, original: str) -> ParagraphEdit | None:
        """The record whose ``original`` matches the paragraph currently in the document."""
        key = normalize(original)
        for e in self.edits:
            if e.file == file and normalize(e.original) == key:
                return e
        return None

    def find_id(self, edit_id: str) -> ParagraphEdit | None:
        for e in self.edits:
            if e.id == edit_id:
                return e
        return None

    def match(self, file: str, original: str, near_line: int,
              partial: bool = False) -> Match | None:
        """Exact match on the current text, then a match on any historical version,
        then a nearby record (same file, close line) whose text still overlaps
        strongly — so history survives hand edits in the editor."""
        e = self.find(file, original)
        if e is not None and e.partial == partial:
            return Match(e, True)
        key = normalize(original)
        for e in self.edits:
            if (e.file == file and e.partial == partial
                    and any(normalize(v.text) == key for v in e.history)):
                return Match(e, False)
        tokens = textmatcher.tokens_latex(original)
        if len(tokens) < 3:
            return None
        nearby = [e for e in self.edits
                  if e.file == file and e.partial == partial and abs(e.line_hint - near_line) <= 3]
        best: tuple[ParagraphEdit, float] | None = None
        for e in nearby:
            candidate = textmatcher.tokens_latex(e.original)
            # A sentence must not adopt a whole-paragraph record (or vice versa).
            longer = max(len(tokens), len(candidate))
            shorter = min(len(tokens), len(candidate))
            if longer == 0 or shorter / longer < 0.5:
                continue
            score = textmatcher.score(tokens, candidate)
            if score >= 0.6 and score > (best[1] if best else 0.0):
                best = (e, score)
        return Match(best[0], False) if best else None

    # -- mutation ----------------------------------------------------------

    def upsert(self, edit: ParagraphEdit) -> None:
        for i, e in enumerate(self.edits):
            if e.id == edit.id:
                self.edits[i] = edit
                return
        self.edits.append(edit)

    def remove(self, edit_id: str) -> None:
        self.edits = [e for e in self.edits if e.id != edit_id]

    @property
    def pending_count(self) -> int:
        """Drafts that differ from the document and have not been applied yet."""
        return sum(1 for e in self.edits if not e.is_in_sync)


def project_edits_path(project_path: str | os.PathLike) -> Path:
    return Path(str(project_path)) / FILE_NAME
