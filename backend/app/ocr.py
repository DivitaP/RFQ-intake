"""Scan path: image / image-only PDF -> text lines with a per-line confidence.

Vision LLMs don't expose token confidences, so confidence is derived from self-agreement:
the page is transcribed twice (temperature 0 and 0.4) and each line of pass A is scored by its
best fuzzy match in pass B. Unstable lines (blur, noise, skew) disagree between passes and get
low confidence, which the pipeline multiplies into every field from that line.

The output is a plain transcription. Parsing still goes through the same rules -> LLM-fill ->
validation path as text PDFs: one pipeline, two ways in.
"""
import difflib
import io
from concurrent.futures import ThreadPoolExecutor

import pdfplumber

from . import llmclient

MAX_SCAN_PAGES = 5
RENDER_DPI = 150

PROMPT = ("Transcribe this scanned purchasing document exactly, line by line, top to bottom. "
          "Keep original abbreviations, symbols (\", #, /) and spacing between columns as single spaces. "
          "Do not normalize, correct or summarize. One array element per printed line.")
SCHEMA = {"type": "ARRAY", "items": {"type": "STRING"}}


class ScanNotSupported(Exception):
    pass


def page_images(data: bytes, kind: str) -> list[tuple[bytes, str]]:
    if kind == "image":
        mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        return [(data, mime)]
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:MAX_SCAN_PAGES]:
            buf = io.BytesIO()
            page.to_image(resolution=RENDER_DPI).original.convert("L").save(buf, "PNG", optimize=True)
            out.append((buf.getvalue(), "image/png"))
    return out


def agreement(a: list[str], b: list[str]) -> list[float]:
    """Token-level: one flipped character in "A234" vs "A23A" breaks a whole token, which is what
    matters for a spec field. Character-level ratios are too forgiving (~0.9 for that line)."""
    toks_b = [x.upper().split() for x in b]
    scores = []
    for line in a:
        t = line.upper().split()
        best = max((difflib.SequenceMatcher(None, t, m, autojunk=False).ratio() for m in toks_b), default=0.0)
        scores.append(round(best, 3))
    return scores


def extract_lines(data: bytes, kind: str) -> list[tuple[str, float]]:
    if not llmclient.available():
        raise ScanNotSupported("scanned documents need an LLM API key (vision transcription)")
    lines: list[tuple[str, float]] = []
    for img in page_images(data, kind):
        with ThreadPoolExecutor(2) as ex:  # both passes in parallel: halves scan latency
            fa = ex.submit(llmclient.generate_json, PROMPT, SCHEMA, image=img, temperature=0.0)
            fb = ex.submit(llmclient.generate_json, PROMPT, SCHEMA, image=img, temperature=0.4)
            a, b = fa.result(), fb.result()
        a = [str(x) for x in a if str(x).strip()] if isinstance(a, list) else []
        b = [str(x) for x in b if str(x).strip()] if isinstance(b, list) else []
        lines.extend(zip(a, agreement(a, b)))
    return lines
