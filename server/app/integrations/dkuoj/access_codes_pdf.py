"""The printable access-code sheet: 8 slips per A4 page (2 x 4), cut lines between them."""
from __future__ import annotations

import io
import unicodedata
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

COLS, ROWS = 2, 4
PER_PAGE = COLS * ROWS
MARGIN_X, TOP, BOTTOM = 28, 100, 28
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


def _header(c: canvas.Canvas, title: str, course: str, stamp: str, width: float, height: float) -> None:
    w = width - 2 * MARGIN_X
    c.setFillGray(0)
    _draw(c, MARGIN_X, height - 40, title, "Helvetica-Bold", 16, w)
    _draw(c, MARGIN_X, height - 56, course, "Helvetica", 10, w)
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN_X, height - 70, f"Generated {stamp} UTC")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN_X, height - 84, NOTE)


def _slip(c: canvas.Canvas, x: float, y: float, w: float, h: float, last: str, first: str, netid: str,
          code: str) -> None:
    pad = 14
    inner = w - 2 * pad
    c.setFillGray(0.35)
    c.setFont("Helvetica", 8)
    for label, dy in (("Last name", 22), ("First name", 54), ("NetID", 86)):
        c.drawString(x + pad, y + h - dy, label)
    c.setFillGray(0)
    for value, dy in ((last, 35), (first, 67), (netid, 99)):
        _draw(c, x + pad, y + h - dy, value, "Helvetica-Bold", 12, inner)
    c.setFillGray(0.35)
    c.setFont("Helvetica", 8)
    c.drawString(x + pad, y + h - 124, "Access code")
    c.setFillGray(0)
    c.setFont("Courier-Bold", 34)
    c.drawString(x + pad, y + 18, clean(code, 16))


def render(title: str, course: str, generated: datetime, students: list[dict]) -> bytes:
    """students: dicts with last, first, netid, code (already in the order to print)."""
    buf = io.BytesIO()
    width, height = A4
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
    c.setTitle(clean(title, 100).encode("latin-1", "replace").decode("latin-1") or "Access codes")
    stamp = generated.strftime("%Y-%m-%d %H:%M")
    cw, ch = (width - 2 * MARGIN_X) / COLS, (height - TOP - BOTTOM) / ROWS
    for start in range(0, len(students), PER_PAGE):
        _header(c, title, course, stamp, width, height)
        c.setLineWidth(0.25)
        c.setStrokeGray(0.6)
        c.setDash(3, 3)
        for i in range(COLS + 1):
            c.line(MARGIN_X + i * cw, BOTTOM, MARGIN_X + i * cw, height - TOP)
        for j in range(ROWS + 1):
            c.line(MARGIN_X, height - TOP - j * ch, width - MARGIN_X, height - TOP - j * ch)
        c.setDash()
        for k, s in enumerate(students[start:start + PER_PAGE]):
            row, col = divmod(k, COLS)
            _slip(c, MARGIN_X + col * cw, height - TOP - (row + 1) * ch, cw, ch,
                  s["last"], s["first"], s["netid"], s["code"])
        c.showPage()
    c.save()
    return buf.getvalue()
