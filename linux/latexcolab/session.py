"""The paragraph currently open in the two-pane rewrite window.

Port of ``ParagraphSession`` from AppModel.swift.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable

from .core.budget import EditSuggestion
from .core.compare import Issue, SemanticComparison
from .core.edits import ParagraphVersion, normalize
from .core.paragraphs import ParagraphRange
from .core.provenance import Provenance

SCOPE_PARAGRAPH = "paragraph"
SCOPE_SENTENCE = "sentence"
SCOPE_SELECTION = "selection"

_SCOPE_LABELS = {
    SCOPE_PARAGRAPH: "Paragraph",
    SCOPE_SENTENCE: "Sentence",
    SCOPE_SELECTION: "Selection",
}


class ParagraphSession:
    """Granularity of the text is ``scope``; ``original`` is what stands in the
    document and ``draft`` is the rewrite."""

    def __init__(self, file: str, rng: ParagraphRange, original: str, draft: str,
                 max_words: int, instructions: str, edit_id: str | None, applied: bool,
                 scope: str = SCOPE_PARAGRAPH, container: str | None = None,
                 span_offset: int = 0):
        self.id = str(uuid.uuid4())
        self.file = file
        self.scope = scope
        #: The whole paragraph (or line range) that contains the edited text.
        self.container = container if container is not None else original
        #: Offset of ``original`` inside ``container`` (0 for whole paragraphs).
        self.span_offset = span_offset
        self.range = rng
        self.original = original
        self._draft = draft
        self.max_words = max_words
        self.instructions = instructions
        self.edit_id = edit_id
        self.applied = applied

        #: Which parts of ``draft`` the AI wrote; re-aligned on every change.
        self.provenance = Provenance()
        #: Set by the model just before assigning an AI-produced draft.
        self.next_draft_change_is_ai = False

        self.is_busy = False
        self.message = ""
        self.message_is_error = False
        self.saved_at: datetime | None = None

        #: Earlier versions of this paragraph, oldest first.
        self.history: list[ParagraphVersion] = []

        #: AI-fix outcome: rewordings the budget did not allow.
        self.dropped_suggestions: list[EditSuggestion] = []
        self.last_fix_summary: str | None = None

        #: Semantic comparison of the two sides.
        self.comparison: SemanticComparison | None = None
        self.integrity_issues: list[Issue] = []
        self.is_comparing = False
        self.show_comparison = False
        self.compared_original: str | None = None
        self.compared_draft: str | None = None

        #: Structural differences that blocked the last Apply.
        self.pending_structural: list[str] = []

        self.on_changed: Callable[[], None] | None = None
        self.on_updated: Callable[[], None] | None = None

    # -- draft -------------------------------------------------------------

    @property
    def draft(self) -> str:
        return self._draft

    @draft.setter
    def draft(self, value: str) -> None:
        if value == self._draft:
            return
        old = self._draft
        self._draft = value
        self.provenance = self.provenance.remapped(old, value, self.next_draft_change_is_ai)
        self.next_draft_change_is_ai = False
        if self.on_changed:
            self.on_changed()

    def set_draft_silently(self, value: str) -> None:
        """Assign without re-aligning provenance (used when loading a record)."""
        self._draft = value

    # -- derived -----------------------------------------------------------

    @property
    def is_in_sync(self) -> bool:
        return normalize(self.original) == normalize(self.draft)

    @property
    def is_partial(self) -> bool:
        return self.scope != SCOPE_PARAGRAPH

    @property
    def scope_label(self) -> str:
        return _SCOPE_LABELS.get(self.scope, "Paragraph")

    @property
    def comparison_is_stale(self) -> bool:
        """True when either side changed after the last comparison ran."""
        if not self.show_comparison or self.compared_draft is None:
            return False
        return self.compared_draft != self.draft or self.compared_original != self.original

    def context(self, chars: int = 70) -> tuple[str, str]:
        """Up to ``chars`` characters of the paragraph before/after the edited span."""
        c = self.container
        start = max(0, min(self.span_offset, len(c)))
        end = min(len(c), start + len(self.original))
        before_start = max(0, start - chars)
        before = c[before_start:start]
        after = c[end:end + min(chars, len(c) - end)]

        def squash(t: str) -> str:
            return t.replace("\n", " ").replace("  ", " ")

        return (("…" if start > chars else "") + squash(before),
                squash(after) + ("…" if len(c) - end > chars else ""))

    def note(self, text: str, error: bool = False) -> None:
        self.message = text
        self.message_is_error = error
        if self.on_updated:
            self.on_updated()

    def touch(self) -> None:
        if self.on_updated:
            self.on_updated()
