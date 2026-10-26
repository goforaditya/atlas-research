"""Statement page location and raw table grids with bounding boxes (Section 4, Stage 4). Code only.

The grid is built from PyMuPDF word positions: words are grouped into lines, the label is the text before the
first numeric cell, and numeric cells snap to column centres found across the page. Values are left as the
printed strings; parse_cell_decimal and normalise_units turn them into Decimals later.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pymupdf as fitz

from backend.engine.unit_normaliser import UnitHeaderError, detect_unit

STATEMENT_TITLES: dict[str, re.Pattern[str]] = {
    "balance_sheet": re.compile(r"consolidated (balance sheets?|statements? of financial position)", re.I),
    "profit_and_loss": re.compile(r"consolidated (statements? of profit and loss|statements? of operations|statements? of income)", re.I),
    "cash_flow": re.compile(r"consolidated (statements? of )?cash flows?( statements?)?", re.I),
}
NUMERIC = re.compile(r"^\(?[-−]?[₹$]?\d[\d,]*(\.\d+)?\)?$|^[-—–]$")
UNIT_LINE = re.compile(r"(in|amounts? in)[^\n]{0,40}\b(crores?|lakhs?|lacs?|millions?|thousands?)\b", re.I)


@dataclass(frozen=True)
class PageScore:
    page: int  # 1-based
    score: float


@dataclass
class StatementPages:
    best: dict[str, Optional[PageScore]] = field(default_factory=dict)
    runner_up: dict[str, Optional[PageScore]] = field(default_factory=dict)


@dataclass(frozen=True)
class Cell:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points, top-left origin


@dataclass
class RawTable:
    page: int
    page_size: tuple[float, float]
    unit_header: Optional[str]
    rows: list[list[Optional[Cell]]]  # column 0 is the label; others are numeric columns (None where empty)

    @property
    def grid(self) -> list[list[str]]:
        return [[c.text if c else "" for c in row] for row in self.rows]


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _score_page(text: str, pattern: re.Pattern[str]) -> float:
    head = text[:500]
    score = 0.0
    if pattern.search(head):
        score += 5
    score += min(len(pattern.findall(text)), 3)
    if re.search(r"\bstandalone\b", head, re.I):
        score -= 6
    if re.search(r"\b(contents|index)\b", head, re.I) or re.search(r"notes to (the )?consolidated", head, re.I):
        score -= 4
    numbers = len(re.findall(r"\d[\d,]*\.\d+|\(\d[\d,]*\)|\d{1,3}(?:,\d{2,3})+", text))
    return score + min(numbers / 40, 3)


def locate_statement_pages(pdf_path: str | Path) -> StatementPages:
    """Score every page for each consolidated statement title; prefer consolidated over standalone."""
    out = StatementPages()
    with fitz.open(pdf_path) as doc:
        texts = [p.get_text() for p in doc]
    for name, pat in STATEMENT_TITLES.items():
        scored = sorted((PageScore(i + 1, _score_page(t, pat)) for i, t in enumerate(texts)), key=lambda s: -s.score)
        scored = [s for s in scored if s.score >= 5]
        out.best[name] = scored[0] if scored else None
        out.runner_up[name] = scored[1] if len(scored) > 1 else None
    return out


def detect_unit_header(text: str) -> Optional[str]:
    """The printed unit line (e.g. '(₹ in crore)'), validated by detect_unit; None when absent or unrecognised."""
    for m in UNIT_LINE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        line = text[line_start: line_end if line_end != -1 else None].strip()
        try:
            detect_unit(line)
            return line
        except UnitHeaderError:
            continue
    return None


def _lines(words: list[tuple]) -> list[list[tuple]]:
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: list[list[tuple]] = []
    for w in words:
        yc = (w[1] + w[3]) / 2
        if lines and abs(((lines[-1][0][1] + lines[-1][0][3]) / 2) - yc) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in lines]


def _cluster(xs: list[float], tol: float = 18) -> list[float]:
    centres: list[list[float]] = []
    for x in sorted(xs):
        if centres and x - centres[-1][-1] <= tol:
            centres[-1].append(x)
        else:
            centres.append([x])
    return [sum(c) / len(c) for c in centres if len(c) >= 2] or [sum(c) / len(c) for c in centres]


def extract_raw_table(pdf_path: str | Path, page: int) -> RawTable:
    """extract_raw_tables_from_pdf for one page: label + numeric columns with per-cell bounding boxes."""
    with fitz.open(pdf_path) as doc:
        pg = doc[page - 1]
        words = pg.get_text("words")
        text = pg.get_text()
        size = (pg.rect.width, pg.rect.height)
    parsed: list[tuple[Cell, list[Cell]]] = []
    for line in _lines(words):
        idx = next((i for i, w in enumerate(line) if NUMERIC.match(w[4]) and i > 0), None)
        if idx is None:
            label_words, nums = line, []
        else:
            label_words, nums = line[:idx], [w for w in line[idx:] if NUMERIC.match(w[4])]
        if not label_words:
            continue
        label = Cell(" ".join(w[4] for w in label_words),
                     (min(w[0] for w in label_words), min(w[1] for w in label_words),
                      max(w[2] for w in label_words), max(w[3] for w in label_words)))
        parsed.append((label, [Cell(w[4], (w[0], w[1], w[2], w[3])) for w in nums]))
    centres = _cluster([c.bbox[2] for _, nums in parsed for c in nums])
    rows: list[list[Optional[Cell]]] = []
    for label, nums in parsed:
        row: list[Optional[Cell]] = [label, *([None] * len(centres))]
        for c in nums:
            col = min(range(len(centres)), key=lambda i: abs(centres[i] - c.bbox[2]))
            row[col + 1] = c
        rows.append(row)
    return RawTable(page=page, page_size=size, unit_header=detect_unit_header(text), rows=rows)


def render_page_png(pdf_path: str | Path, page: int, out_path: str | Path, dpi: int = 144) -> Path:
    out = Path(out_path)
    with fitz.open(pdf_path) as doc:
        doc[page - 1].get_pixmap(dpi=dpi).save(out)
    return out
