"""PDF geometry and rasterisation — the Linux stand-in for PDFKit.

Everything the app needs from a PDF is: page sizes, per-word bounding boxes (to
map a click or a drag selection to text) and a rendered bitmap. Poppler's
command line tools provide all three and ship with every desktop distribution:

* ``pdftotext -bbox-layout`` → words and lines with boxes, in PDF points with a
  top-left origin — the same space SyncTeX box records are converted to.
* ``pdftoppm -png``          → a page bitmap at an arbitrary resolution.

Coordinates in this module are always PDF points measured from the top-left of
the page, so they can be handed to :mod:`latexcolab.core.synctex` unchanged.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import process


class PDFError(Exception):
    pass


#: XML 1.0 forbids most control characters, but pdftotext copies glyph text
#: straight through — a font whose ToUnicode maps to U+0001 (common in maths
#: and symbol fonts) produces a document ElementTree refuses outright. Dropping
#: those characters costs nothing: the affected "word" carries no text anyway.
_INVALID_XML_CHARS = re.compile(
    "[^\u0009\u000A\u000D\u0020-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def mid_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def mid_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def inset(self, dx: float, dy: float) -> "Box":
        return Box(self.x0 + dx, self.y0 + dy, self.x1 - dx, self.y1 - dy)

    def union(self, other: "Box") -> "Box":
        return Box(min(self.x0, other.x0), min(self.y0, other.y0),
                   max(self.x1, other.x1), max(self.y1, other.y1))


@dataclass(frozen=True)
class Word:
    text: str
    box: Box
    line_index: int


@dataclass
class Page:
    index: int          # 0-based
    width: float        # PDF points
    height: float
    words: list[Word] = field(default_factory=list)
    #: One box per text line, in reading order.
    lines: list[Box] = field(default_factory=list)

    def word_at(self, x: float, y: float) -> Word | None:
        for w in self.words:
            if w.box.contains(x, y):
                return w
        return None

    def line_at(self, x: float, y: float) -> int | None:
        """Index of the text line under the point (vertical band, any x)."""
        for i, box in enumerate(self.lines):
            if box.y0 <= y <= box.y1:
                if box.x0 - 24 <= x <= box.x1 + 24:
                    return i
        return None

    def words_of_line(self, line_index: int) -> list[Word]:
        return [w for w in self.words if w.line_index == line_index]

    def text_in_band(self, y: float, half_height: float = 26) -> str:
        """Text of every word whose vertical centre is within the band —
        the equivalent of PDFKit's ``selection(for: band).string``."""
        picked = [w for w in self.words if abs(w.box.mid_y - y) <= half_height]
        return " ".join(w.text for w in picked)

    def words_in_rect(self, box: Box) -> list[Word]:
        return [w for w in self.words
                if w.box.mid_y >= box.y0 and w.box.mid_y <= box.y1
                and w.box.x1 > box.x0 and w.box.x0 < box.x1]

    def words_between(self, start: tuple[float, float], end: tuple[float, float]) -> list[Word]:
        """Words in reading order between two page points (a drag selection)."""
        if not self.words:
            return []
        s = self._reading_index(start)
        e = self._reading_index(end)
        if s > e:
            s, e = e, s
        return self.words[s:e + 1]

    def _reading_index(self, point: tuple[float, float]) -> int:
        x, y = point
        best = 0
        best_key = None
        for i, w in enumerate(self.words):
            dy = 0.0 if w.box.y0 <= y <= w.box.y1 else min(abs(w.box.y0 - y), abs(w.box.y1 - y))
            dx = 0.0 if w.box.x0 <= x <= w.box.x1 else min(abs(w.box.x0 - x), abs(w.box.x1 - x))
            key = (dy * 4 + dx)
            if best_key is None or key < best_key:
                best_key = key
                best = i
        return best


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class PDFDocument:
    """Lazily-parsed geometry for one PDF file."""

    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        try:
            self.mtime = os.path.getmtime(self.path)
        except OSError as exc:
            raise PDFError(f"cannot read {self.path}: {exc}") from exc
        self.pages: list[Page] = []
        #: Why word boxes are unavailable, when they are.
        self.text_error: str | None = None
        self._load_geometry()

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page(self, index: int) -> Page | None:
        if 0 <= index < len(self.pages):
            return self.pages[index]
        return None

    @property
    def has_text(self) -> bool:
        """False for a PDF poppler cannot extract words from (a scan, or fonts
        with no ToUnicode map). Clicks still map through SyncTeX, but sentence
        and selection editing need the word boxes."""
        return any(page.words for page in self.pages)

    # -- geometry ----------------------------------------------------------

    def _load_geometry(self) -> None:
        pdftotext = process.which("pdftotext")
        if not pdftotext:
            self.text_error = "pdftotext not found — install poppler-utils"
        else:
            r = process.run(pdftotext, ["-bbox-layout", self.path, "-"], timeout=60)
            if not r.ok:
                self.text_error = "pdftotext failed: " + r.output.strip()[:200]
            elif "<page" not in r.output:
                self.text_error = "this PDF has no extractable text"
            else:
                try:
                    self._parse_bbox(_INVALID_XML_CHARS.sub("", r.output))
                    return
                except ET.ParseError as exc:
                    self.text_error = f"could not read the text layout: {exc}"
        # No poppler-utils, or an image-only PDF: fall back to page sizes only,
        # so rendering and SyncTeX clicks still work (text matching does not).
        self._load_page_sizes()

    def _parse_bbox(self, xml: str) -> None:
        root = ET.fromstring(xml)
        pages: list[Page] = []
        for page_el in root.iter():
            if _local(page_el.tag) != "page":
                continue
            page = Page(len(pages),
                        float(page_el.get("width", "612")),
                        float(page_el.get("height", "792")))
            for line_el in page_el.iter():
                if _local(line_el.tag) != "line":
                    continue
                line_box: Box | None = None
                line_index = len(page.lines)
                added = False
                for word_el in line_el:
                    if _local(word_el.tag) != "word":
                        continue
                    text = (word_el.text or "").strip()
                    if not text:
                        continue
                    box = Box(float(word_el.get("xMin", "0")), float(word_el.get("yMin", "0")),
                              float(word_el.get("xMax", "0")), float(word_el.get("yMax", "0")))
                    page.words.append(Word(text, box, line_index))
                    line_box = box if line_box is None else line_box.union(box)
                    added = True
                if added and line_box is not None:
                    page.lines.append(line_box)
            pages.append(page)
        if not pages:
            raise ET.ParseError("no pages")
        self.pages = pages

    def _load_page_sizes(self) -> None:
        pdfinfo = process.which("pdfinfo")
        count, width, height = 1, 612.0, 792.0
        if pdfinfo:
            r = process.run(pdfinfo, [self.path], timeout=30)
            if r.ok:
                m = re.search(r"^Pages:\s+(\d+)", r.output, re.M)
                if m:
                    count = int(m.group(1))
                m = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+)", r.output, re.M)
                if m:
                    width, height = float(m.group(1)), float(m.group(2))
        self.pages = [Page(i, width, height) for i in range(count)]

    # -- rasterisation -----------------------------------------------------

    def render_page_png(self, index: int, dpi: float) -> bytes:
        """Render one page (0-based) to PNG bytes at ``dpi``."""
        return render_png(self.path, index, dpi)

    def page_text(self, index: int) -> str:
        page = self.page(index)
        return " ".join(w.text for w in page.words) if page else ""


def render_png(pdf_path: str, index: int, dpi: float) -> bytes:
    """Render page ``index`` (0-based) of ``pdf_path`` to PNG bytes.

    ``pdftocairo`` streams to stdout, so it is preferred; ``pdftoppm`` treats a
    ``-`` output argument as a *filename prefix* rather than stdout, so it is
    given a real temporary file.
    """
    import subprocess
    import tempfile

    page_no = str(index + 1)
    common = ["-png", "-r", f"{dpi:.4f}", "-f", page_no, "-l", page_no,
              "-singlefile", "-cropbox"]

    pdftocairo = process.which("pdftocairo")
    if pdftocairo:
        try:
            proc = subprocess.run([pdftocairo, *common, pdf_path, "-"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PDFError(f"pdftocairo failed: {exc}") from exc
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout

    pdftoppm = process.which("pdftoppm")
    if not pdftoppm:
        raise PDFError("Neither pdftocairo nor pdftoppm was found — "
                       "install poppler-utils to preview the PDF.")
    with tempfile.TemporaryDirectory(prefix="latexcolab-render-") as tmp:
        prefix = os.path.join(tmp, "page")
        try:
            proc = subprocess.run([pdftoppm, *common, pdf_path, prefix],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PDFError(f"pdftoppm failed: {exc}") from exc
        out = prefix + ".png"
        if proc.returncode != 0 or not os.path.exists(out):
            raise PDFError("pdftoppm failed: "
                           + proc.stderr.decode("utf-8", "replace")[:400])
        with open(out, "rb") as fh:
            return fh.read()


def poppler_available() -> bool:
    return (process.which("pdftocairo") or process.which("pdftoppm")) is not None
