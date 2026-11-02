"""Turn raw document lines into candidate line-item strings.

Rules:
  1. An item line needs quantity evidence (row prefix "<n> <qty> <uom>", "<qty> <uom>", or "QTY n")
     plus an item type or a size. Notes like "all flanges to match pipe schedule" have no qty -> dropped.
  2. A continuation line (description wrapped onto the next line) is short, has no qty evidence,
     isn't boilerplate, and directly follows an item line -> appended to that item.
  3. Anything else (headers, greetings, notes, signatures) breaks the chain, so a stray line after
     the signature can't attach to the last item.
"""
import re

from . import parse, vocab

NOISE = re.compile(
    r"^(ITEM|LINE|QTY\s+UOM|DESCRIPTION|REQUEST FOR QUOTE|RFQ|PAGE \d|PLEASE|HI\b|HELLO|DEAR|NOTE|NOTES|"
    r"THANKS|THANK YOU|REGARDS|BEST|FROM:|TO:|CC:|SUBJECT:|DATE:|DELIVERY|SHIP TO|BILL TO)"
)
MAX_CONTINUATION_WORDS = 5


def has_qty(c: str) -> bool:
    return bool(vocab.ROW_PREFIX_RE.match(c) or vocab.QTY_UOM_RE.search(c) or vocab.QTY_LABEL_RE.search(c))


def is_item(c: str) -> bool:
    if NOISE.match(c) or not has_qty(c):
        return False
    # any recognizable PVF attribute; a line with qty but an unknown type still goes through
    # (as a review item / LLM candidate) instead of silently disappearing
    return any(x is not None for x in (
        parse.parse_item_type(c), parse.parse_size(c), parse.parse_class(c),
        parse.parse_material(c)[0], parse.parse_end(c),
    ))


def is_continuation(c: str) -> bool:
    if NOISE.match(c) or has_qty(c) or vocab.ROW_PREFIX_RE.match(c):
        return False
    if re.search(r"[.,:;!?]$", c):  # prose ("Need MTRs with shipment. Thanks,"), not a wrapped spec
        return False
    return 0 < len(c.split()) <= MAX_CONTINUATION_WORDS


def segment_spans(lines: list[str]) -> list[tuple[str, list[int]]]:
    """Returns (item_text, source_line_indices) so callers can combine per-line OCR confidence."""
    items: list[tuple[str, list[int]]] = []
    prev_is_item = False
    for idx, raw in enumerate(lines):
        line = raw.strip()
        c = parse.clean(line)
        if not c:
            prev_is_item = False  # blank line = paragraph break (emails): nothing continues across it
            continue
        if is_item(c):
            items.append((line, [idx]))
            prev_is_item = True
        elif prev_is_item and is_continuation(c):
            text, src = items[-1]
            items[-1] = (f"{text} {line}", src + [idx])
        else:
            prev_is_item = False
    return items


def segment(lines: list[str]) -> list[str]:
    return [t for t, _ in segment_spans(lines)]
