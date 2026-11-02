"""Ingest: detect text PDF vs scan/image and pull raw lines."""
import io

import pdfplumber

MIN_CHARS_PER_PAGE = 40
MAX_PAGES = 20


def sniff(data: bytes, filename: str) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
        return "image"
    from .email_ingest import looks_like_eml
    if looks_like_eml(data, filename):
        return "email"
    raise ValueError(f"unsupported file type: {filename}")


def pdf_lines(data: bytes) -> tuple[list[str], bool]:
    """Returns (lines, is_scanned). Scanned = PDF with (almost) no text layer."""
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = pdf.pages[:MAX_PAGES]
        chars = 0
        for page in pages:
            text = page.extract_text(layout=False) or ""
            chars += len(text.strip())
            lines.extend(l for l in text.splitlines() if l.strip())
        scanned = chars < MIN_CHARS_PER_PAGE * max(1, len(pages))
    return lines, scanned
