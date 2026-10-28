"""The printable access-code sheet: a class list for the invigilator (28 rows a page) and 12 slips to cut out per A4 page (2 x 6)."""
from __future__ import annotations

import io
import unicodedata
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

COLS, ROWS = 2, 6
PER_PAGE = COLS * ROWS
LIST_ROWS = 28
LAYOUTS = ("both", "list", "slips")
MARGIN_X = 28
NOTE = "Do not share. One code per student."


CJK_FONT = "STSong-Light"


def clean(text: str, limit: int = 200) -> str:
    out = [" " if unicodedata.category(ch)[0] == "C" else ch
           for ch in unicodedata.normalize("NFC", str(text or ""))]
    return " ".join("".join(out).split())[:limit]


def font_for(text: str, latin: str) -> str:
    """The Latin font, or the built-in CJK font for a text with characters outside Latin-1."""
    if all(ord(ch) < 256 for ch in text):
        return latin
    if CJK_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))
    return CJK_FONT


def fit(text: str, font: str, size: float, width: float) -> tuple[str, str]:
    """(text, font) shortened with "..." until it fits the width."""
    text = clean(text)
    font = font_for(text, font)
    if stringWidth(text, font, size) <= width:
        return text, font
    while text and stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "...", font


def _draw(c: canvas.Canvas, x: float, y: float, text: str, font: str, size: float, width: float) -> None:
    text, font = fit(text, font, size, width)
    c.setFont(font, size)
    c.drawString(x, y, text)


def _footer(c: canvas.Canvas, width: float, page: int, pages: int) -> None:
    c.setFillGray(0.4)
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, 20, f"Page {page} of {pages}")
    c.setFillGray(0)


LIST_COLS = (("#", 26), ("Last name", 150), ("First name", 130), ("NetID", 82), ("Access code", 96), ("Present", 39))
LIST_TOP, LIST_ROW_H, LIST_LEFT = 100, 24.0, 36.0


def _list_page(c: canvas.Canvas, title: str, course: str, stamp: str, width: float, height: float,
               rows: list[dict], first_no: int) -> None:
    w = width - 2 * LIST_LEFT
    c.setFillGray(0)
    _draw(c, LIST_LEFT, height - 40, title, "Helvetica-Bold", 15, w)
    _draw(c, LIST_LEFT, height - 56, course, "Helvetica", 10, w)
    c.setFont("Helvetica", 9)
    c.drawString(LIST_LEFT, height - 70, f"Class list for the invigilator. Generated {stamp} UTC. {NOTE}")
    top = height - LIST_TOP
    xs = [LIST_LEFT]
    for _, cw in LIST_COLS:
        xs.append(xs[-1] + cw)
    c.setFillGray(0.92)
    c.rect(LIST_LEFT, top - 18, w, 18, stroke=0, fill=1)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 9)
    for (label, _), x in zip(LIST_COLS, xs):
        c.drawString(x + 4, top - 12.5, label)
    c.setStrokeGray(0.75)
    c.setLineWidth(0.5)
    for k, s in enumerate(rows):
        y = top - 18 - (k + 1) * LIST_ROW_H
        c.line(LIST_LEFT, y, LIST_LEFT + w, y)
        base = y + 8
        c.setFillGray(0.4)
        c.setFont("Helvetica", 9)
        c.drawString(xs[0] + 4, base, str(first_no + k))
        c.setFillGray(0)
        _draw(c, xs[1] + 4, base, s["last"], "Helvetica", 11, LIST_COLS[1][1] - 8)
        _draw(c, xs[2] + 4, base, s["first"], "Helvetica", 11, LIST_COLS[2][1] - 8)
        _draw(c, xs[3] + 4, base, s["netid"], "Helvetica", 11, LIST_COLS[3][1] - 8)
        c.setFont("Courier-Bold", 14)
        c.drawString(xs[4] + 4, base - 1, clean(s["code"], 16))
        c.setStrokeGray(0.3)
        box = 12
        c.rect(xs[5] + (LIST_COLS[5][1] - box) / 2, y + (LIST_ROW_H - box) / 2, box, box, stroke=1, fill=0)
        c.setStrokeGray(0.75)
    c.line(LIST_LEFT, top - 18, LIST_LEFT + w, top - 18)


def _slip(c: canvas.Canvas, x: float, y: float, w: float, h: float, title: str, last: str, first: str,
          netid: str, code: str) -> None:
    pad = 14
    inner = w - 2 * pad
    c.setFillGray(0.35)
    c.setFont("Helvetica", 7)
    for label, dy in (("Last name", 14), ("First name", 38), ("NetID", 62)):
        c.drawString(x + pad, y + h - dy, label)
    c.setFillGray(0)
    for value, dy in ((last, 26), (first, 50), (netid, 74)):
        _draw(c, x + pad, y + h - dy, value, "Helvetica-Bold", 12, inner)
    c.setFillGray(0.35)
    c.setFont("Helvetica", 7)
    c.drawString(x + pad, y + 44, "Access code")
    c.setFillGray(0)
    c.setFont("Courier-Bold", 26)
    c.drawString(x + pad, y + 18, clean(code, 16))
    c.setFillGray(0.4)
    _draw(c, x + pad, y + 7, title, "Helvetica", 6.5, inner)
    c.setFillGray(0)


def _slips_page(c: canvas.Canvas, title: str, width: float, height: float, rows: list[dict]) -> None:
    top, bottom = height - 28, 36
    cw, ch = (width - 2 * MARGIN_X) / COLS, (top - bottom) / ROWS
    c.setLineWidth(0.25)
    c.setStrokeGray(0.6)
    c.setDash(3, 3)
    for i in range(COLS + 1):
        c.line(MARGIN_X + i * cw, bottom, MARGIN_X + i * cw, top)
    for j in range(ROWS + 1):
        c.line(MARGIN_X, top - j * ch, width - MARGIN_X, top - j * ch)
    c.setDash()
    for k, s in enumerate(rows):
        row, col = divmod(k, COLS)
        _slip(c, MARGIN_X + col * cw, top - (row + 1) * ch, cw, ch, title, s["last"], s["first"], s["netid"], s["code"])


def render(title: str, course: str, generated: datetime, students: list[dict], layout: str = "both") -> bytes:
    """students: dicts with last, first, netid, code (already in the order to print).
    layout: "list" (invigilator's class list), "slips" (to cut out) or "both" (list first)."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}")
    buf = io.BytesIO()
    width, height = A4
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    c.setTitle(clean(title, 100).encode("latin-1", "replace").decode("latin-1") or "Access codes")
    stamp = generated.strftime("%Y-%m-%d %H:%M")
    n = len(students)
    list_pages = -(-n // LIST_ROWS) if layout != "slips" else 0
    slip_pages = -(-n // PER_PAGE) if layout != "list" else 0
    pages, page = list_pages + slip_pages, 0
    for start in range(0, n if list_pages else 0, LIST_ROWS):
        page += 1
        _list_page(c, title, course, stamp, width, height, students[start:start + LIST_ROWS], start + 1)
        _footer(c, width, page, pages)
        c.showPage()
    for start in range(0, n if slip_pages else 0, PER_PAGE):
        page += 1
        _slips_page(c, title, width, height, students[start:start + PER_PAGE])
        _footer(c, width, page, pages)
        c.showPage()
    c.save()
    return buf.getvalue()
