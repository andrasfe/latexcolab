"""SyncTeX parser with PDF→source and source→PDF queries (port of SyncTeX.swift).

Coordinates in the file are scaled points; they are converted to bp (PDF points)
measured from the top-left corner of the page, which is the space the PDF view
works in.
"""

from __future__ import annotations

import gzip
import os
from dataclasses import dataclass
from pathlib import Path

#: 1 bp = 72.27/72 pt = 65781.76 sp.
_SP_PER_BP = 65781.76


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def min_x(self) -> float:
        return self.x

    @property
    def min_y(self) -> float:
        return self.y

    @property
    def max_x(self) -> float:
        return self.x + self.width

    @property
    def max_y(self) -> float:
        return self.y + self.height

    def union(self, other: "Rect") -> "Rect":
        x0 = min(self.min_x, other.min_x)
        y0 = min(self.min_y, other.min_y)
        x1 = max(self.max_x, other.max_x)
        y1 = max(self.max_y, other.max_y)
        return Rect(x0, y0, x1 - x0, y1 - y0)


@dataclass(frozen=True)
class SyncTeXBox:
    """A box record (``(``/``[``/``h``/``v``) in bp from the page's top-left."""
    page: int
    tag: int
    line: int
    x: float
    y: float
    width: float
    height: float
    depth: float
    is_horizontal: bool
    nesting: int

    @property
    def min_x(self) -> float:
        return min(self.x, self.x + self.width)

    @property
    def max_x(self) -> float:
        return max(self.x, self.x + self.width)

    @property
    def min_y(self) -> float:
        return self.y - self.height

    @property
    def max_y(self) -> float:
        return self.y + self.depth

    @property
    def area(self) -> float:
        return abs(self.width) * (self.height + self.depth)

    def contains(self, px: float, py: float) -> bool:
        return self.min_x <= px <= self.max_x and self.min_y <= py <= self.max_y

    def distance(self, px: float, py: float) -> tuple[float, float]:
        """Distance from a point to the box rectangle (0 when inside)."""
        dx = self.min_x - px if px < self.min_x else (px - self.max_x if px > self.max_x else 0.0)
        dy = self.min_y - py if py < self.min_y else (py - self.max_y if py > self.max_y else 0.0)
        return dx, dy


@dataclass(frozen=True)
class SyncTeXPoint:
    """A point record (``x``, ``k``, ``g``, ``$``) attached to its innermost box."""
    page: int
    tag: int
    line: int
    x: float
    y: float
    box_index: int | None


@dataclass(frozen=True)
class SyncTeXLocation:
    path: str
    line: int
    tag: int


def read_text_file(path: str | os.PathLike) -> str:
    """Read a text file that may or may not be gzip-compressed."""
    data = Path(path).read_bytes()
    if len(data) > 2 and data[0] == 0x1F and data[1] == 0x8B:
        data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


def locate_file_for_pdf(pdf_path: str | os.PathLike) -> str | None:
    """``main.pdf`` → ``main.synctex.gz`` or ``main.synctex``, whichever exists."""
    stem = os.path.splitext(str(pdf_path))[0]
    for ext in (".synctex.gz", ".synctex"):
        candidate = stem + ext
        if os.path.exists(candidate):
            return candidate
    return None


class SyncTeXScanner:
    def __init__(self, text: str, base_path: str | os.PathLike):
        self.base_path = str(base_path)
        self.inputs: dict[int, str] = {}
        self.boxes: list[SyncTeXBox] = []
        self.points: list[SyncTeXPoint] = []
        #: Indices of boxes that directly own point records (text lines, not containers).
        self.boxes_with_points: set[int] = set()
        self._file_cache: dict[int, str | None] = {}
        self._unit = 1.0
        self._magnification = 1000.0
        self._x_offset = 0.0
        self._y_offset = 0.0
        self._parse(text)

    @classmethod
    def from_file(cls, path: str | os.PathLike, base_path: str | os.PathLike) -> "SyncTeXScanner":
        return cls(read_text_file(path), base_path)

    @property
    def _scale(self) -> float:
        return self._unit * (self._magnification / 1000.0) / _SP_PER_BP

    # -- parsing -----------------------------------------------------------

    def _parse(self, text: str) -> None:
        in_content = False
        page = 0
        stack: list[int] = []

        for raw_line in text.split("\n"):
            if not raw_line:
                continue
            line = raw_line.rstrip("\r")
            if not line:
                continue
            if line.startswith("Input:"):
                rest = line[len("Input:"):]
                colon = rest.find(":")
                if colon > 0:
                    try:
                        tag = int(rest[:colon])
                    except ValueError:
                        continue
                    self.inputs[tag] = rest[colon + 1:]
                continue
            if not in_content:
                if line.startswith("Magnification:"):
                    self._magnification = _to_float(line[14:], 1000.0)
                elif line.startswith("Unit:"):
                    self._unit = _to_float(line[5:], 1.0)
                elif line.startswith("X Offset:"):
                    self._x_offset = _to_float(line[9:], 0.0)
                elif line.startswith("Y Offset:"):
                    self._y_offset = _to_float(line[9:], 0.0)
                elif line.startswith("Content:"):
                    in_content = True
                continue
            if line.startswith("Postamble:"):
                break
            kind = line[0]
            body = line[1:]
            if kind == "{":
                page = _to_int(body, page)
                stack.clear()
            elif kind == "}":
                stack.clear()
            elif kind in ("[", "("):
                box = self._parse_box(body, page, kind == "(", len(stack))
                if box is not None:
                    self.boxes.append(box)
                    stack.append(len(self.boxes) - 1)
                else:
                    # Keep nesting balanced even if a record is malformed.
                    stack.append(-1)
            elif kind in ("]", ")"):
                if stack:
                    stack.pop()
            elif kind in ("h", "v"):
                box = self._parse_box(body, page, kind == "h", len(stack))
                if box is not None:
                    self.boxes.append(box)
            elif kind in ("x", "k", "g", "$", "r"):
                parent = stack[-1] if stack and stack[-1] >= 0 else None
                p = self._parse_point(body, page, parent)
                if p is not None:
                    self.points.append(p)
                    if p.box_index is not None:
                        self.boxes_with_points.add(p.box_index)
            # "!" offsets, "f" form refs, etc. are ignored.

    def _parse_box(self, body: str, page: int, horizontal: bool, nesting: int) -> SyncTeXBox | None:
        parts = body.split(":")
        if len(parts) < 3:
            return None
        tl = parts[0].split(",")
        xy = parts[1].split(",")
        whd = parts[2].split(",")
        if len(tl) != 2 or len(xy) != 2 or len(whd) != 3:
            return None
        try:
            tag, line = int(tl[0]), int(tl[1])
            x, y = float(xy[0]), float(xy[1])
            w, h, d = float(whd[0]), float(whd[1]), float(whd[2])
        except ValueError:
            return None
        s = self._scale
        return SyncTeXBox(page, tag, line,
                          (x + self._x_offset) * s, (y + self._y_offset) * s,
                          w * s, h * s, d * s, horizontal, nesting)

    def _parse_point(self, body: str, page: int, box_index: int | None) -> SyncTeXPoint | None:
        parts = body.split(":")
        if len(parts) < 2:
            return None
        tl = parts[0].split(",")
        xy = parts[1].split(",")
        if len(tl) != 2 or len(xy) != 2:
            return None
        try:
            tag, line = int(tl[0]), int(tl[1])
            x, y = float(xy[0]), float(xy[1])
        except ValueError:
            return None
        s = self._scale
        return SyncTeXPoint(page, tag, line,
                            (x + self._x_offset) * s, (y + self._y_offset) * s, box_index)

    # -- queries -----------------------------------------------------------

    def file_for_tag(self, tag: int) -> str | None:
        """Resolve an input tag to an existing regular file.

        pdfTeX writes empty paths for some inputs ("Input:7:") — those give None.
        """
        if tag in self._file_cache:
            return self._file_cache[tag]
        resolved = self._resolve(tag)
        self._file_cache[tag] = resolved
        return resolved

    def _resolve(self, tag: int) -> str | None:
        raw = (self.inputs.get(tag) or "").strip()
        if not raw:
            return None
        candidates = [raw if raw.startswith("/") else os.path.join(self.base_path, raw)]
        if not raw.endswith(".tex"):
            candidates.append(candidates[0] + ".tex")
        for c in candidates:
            std = os.path.normpath(c)
            if os.path.isfile(std):
                return std
        return None

    def display_query(self, path: str | os.PathLike, first_line: int,
                      last_line: int) -> tuple[int, Rect] | None:
        """Source → PDF.

        Returns the 1-based page and the union rectangle (bp, top-left origin) of
        the text lines produced by the given lines of ``path``. SyncTeX attributes
        a paragraph's line boxes to the blank line after it, so the range is
        extended by one line.
        """
        target = os.path.normpath(str(path))
        tags = {t for t in self.inputs if self.file_for_tag(t) == target}
        if not tags:
            return None
        lo, hi = first_line, last_line + 1

        def is_line(b: SyncTeXBox) -> bool:
            return b.is_horizontal and b.width > 0 and b.height + b.depth <= 60

        hits: set[int] = set()
        for i, b in enumerate(self.boxes):
            if b.tag in tags and lo <= b.line <= hi and is_line(b):
                hits.add(i)
        for p in self.points:
            if p.tag in tags and lo <= p.line <= hi and p.box_index is not None:
                if is_line(self.boxes[p.box_index]):
                    hits.add(p.box_index)
        if not hits:
            return None
        by_page: dict[int, list[SyncTeXBox]] = {}
        for i in hits:
            by_page.setdefault(self.boxes[i].page, []).append(self.boxes[i])
        page = min(by_page)
        rect: Rect | None = None
        for b in by_page[page]:
            r = Rect(b.min_x, b.min_y, b.max_x - b.min_x, b.max_y - b.min_y)
            rect = r if rect is None else rect.union(r)
        assert rect is not None
        return page, rect

    def edit_query(self, page: int, x: float, y: float,
                   tolerance: float = 24) -> SyncTeXLocation | None:
        """PDF → source. ``page`` is 1-based; ``x``/``y`` are bp from the page's top-left.

        Order of preference: the deepest text-line box under the point, then the
        nearest text-line box within ``tolerance`` (clicks between lines), then
        the deepest box of any kind under the point (figures, display math,
        layout containers). The line is refined with the closest point record
        inside the chosen box.
        """
        # Text lines are short boxes owning glue/kern records; page-wide layout
        # boxes can own a stray kern too, so height is part of the test.
        def is_text_line(i: int, b: SyncTeXBox) -> bool:
            return i in self.boxes_with_points and b.height + b.depth <= 60 and b.width > 0

        def deepest_containing(require_points: bool) -> int | None:
            best: int | None = None
            best_key = (-1, float("-inf"))
            for i, b in enumerate(self.boxes):
                if b.page != page or not b.is_horizontal or not b.contains(x, y):
                    continue
                if require_points and not is_text_line(i, b):
                    continue
                key = (b.nesting, -b.area)
                if key[0] > best_key[0] or (key[0] == best_key[0] and key[1] > best_key[1]):
                    best = i
                    best_key = key
            return best

        def nearest_line() -> int | None:
            best: int | None = None
            best_dist = float("inf")
            for i, b in enumerate(self.boxes):
                if b.page != page or not b.is_horizontal or not is_text_line(i, b):
                    continue
                dx, dy = b.distance(x, y)
                if dy > tolerance or dx > tolerance * 6:
                    continue
                dist = dy * 4 + dx
                if dist < best_dist:
                    best_dist = dist
                    best = i
            return best

        idx = deepest_containing(True)
        if idx is None:
            idx = nearest_line()
        if idx is None:
            idx = deepest_containing(False)
        if idx is None:
            return None

        box = self.boxes[idx]
        # Closest point record with a resolvable file wins; else the box itself.
        chosen: tuple[int, int] | None = None
        nearest = float("inf")
        for p in self.points:
            if p.box_index != idx:
                continue
            d = abs(p.x - x)
            if d < nearest and self.file_for_tag(p.tag) is not None:
                nearest = d
                chosen = (p.tag, p.line)
        if chosen is None and self.file_for_tag(box.tag) is not None:
            chosen = (box.tag, box.line)
        if chosen is None:
            return None
        path = self.file_for_tag(chosen[0])
        if path is None:
            return None
        return SyncTeXLocation(path, chosen[1], chosen[0])


def _to_float(s: str, default: float) -> float:
    try:
        return float(s.strip())
    except ValueError:
        return default


def _to_int(s: str, default: int) -> int:
    try:
        return int(s.strip())
    except ValueError:
        return default
