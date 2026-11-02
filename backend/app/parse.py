"""Deterministic, rules-first field extraction for one RFQ line."""
import re

from . import vocab
from .schema import FIELDS, FieldValue

_QUOTES = str.maketrans({"“": '"', "”": '"', "″": '"', "′": "'", "’": "'", "×": "x"})


def clean(line: str) -> str:
    s = line.translate(_QUOTES).upper()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _canon_size(tok: str) -> str | None:
    tok = tok.strip().replace(" ", "-")
    if tok in vocab.DECIMAL_TO_NPS:
        return vocab.DECIMAL_TO_NPS[tok]
    if tok.endswith(".0"):
        tok = tok[:-2]
    return tok if tok in vocab.VALID_NPS else None


def parse_size(s: str) -> str | None:
    sizes: list[tuple[int, int, str]] = []
    for m in vocab.SIZE_RE.finditer(s):
        nps, dn, inch = m.groups()
        canon = None
        if nps:
            canon = _canon_size(nps)
        elif dn:
            canon = vocab.DN_TO_NPS.get(dn)
        elif inch:
            canon = _canon_size(inch)
        if canon:
            sizes.append((m.start(), m.end(), canon))
    if not sizes:
        return None
    # "2" X 1"" (reducers / reducing tees) -> "2x1"
    if len(sizes) >= 2 and re.match(r"^\s*X\s*$", s[sizes[0][1]:sizes[1][0]]):
        return f"{sizes[0][2]}x{sizes[1][2]}"
    return sizes[0][2]


def parse_item_type(s: str) -> str | None:
    for canon, pat in vocab.ITEM_TYPES:
        if re.search(pat, s):
            return canon
    if re.search(r"\b(FLG|FLANGE)\b", s):
        return "flange"  # type unknown -> validation flags it
    if re.search(r"\b(VALVE|VLV)\b", s):
        return "valve"
    return None


def parse_schedule(s: str) -> str | None:
    m = vocab.SCHEDULE_RE.search(s)
    if not m:
        return None
    v = m.group(1) or m.group(2)
    return v if v in {"STD", "XS", "XXS"} else f"SCH {v}"


def parse_class(s: str) -> str | None:
    m = vocab.CLASS_RE.search(s)
    if not m:
        return None
    return m.group(1) or m.group(2)


def parse_material(s: str) -> tuple[str | None, bool]:
    for mat in vocab.MATERIALS:
        if re.search(mat["re"], s):
            return mat["canon"], bool(mat.get("generic"))
    return None, False


def parse_end(s: str) -> str | None:
    for canon, pat in vocab.END_CONNECTIONS:
        if re.search(pat, s):
            return canon
    return None


def parse_qty_uom(s: str) -> tuple[float | None, str | None]:
    m = vocab.ROW_PREFIX_RE.match(s)
    if not m:
        m2 = vocab.QTY_UOM_RE.search(s)
        if m2:
            return float(m2.group(1).replace(",", "")), vocab.UOM_ALIASES[m2.group(2)]
        m3 = vocab.QTY_LABEL_RE.search(s)
        if m3:
            return float(m3.group(1)), None
        return None, None
    return float(m.group(2).replace(",", "")), vocab.UOM_ALIASES[m.group(3)]


GENERIC_MATERIALS = {m["canon"] for m in vocab.MATERIALS if m.get("generic")}


def apply_aliases(s: str, out: dict[str, FieldValue], aliases: list[dict]) -> None:
    """Reviewer-confirmed aliases fill fields the built-in vocabulary left empty (or only generic)."""
    for a in aliases:
        f = a["field"]
        cur = out[f]
        if cur.value is not None and not (f == "material" and cur.value in GENERIC_MATERIALS):
            continue
        if re.search(rf"(?<![A-Z0-9]){re.escape(a['text'])}(?![A-Z0-9])", s):
            out[f] = FieldValue(value=a["value"], source="learned", evidence=a["text"])


def parse_line(line: str, aliases: list[dict] | None = None) -> tuple[dict[str, FieldValue], list[str]]:
    """Returns (fields, notes). Every field present; missing ones have value=None, source='none'."""
    s = clean(line)
    notes: list[str] = []
    out = {f: FieldValue() for f in FIELDS}

    def put(name, value, issue=None):
        if value is not None:
            out[name] = FieldValue(value=value, source="rule", issues=[issue] if issue else [])

    put("item_type", parse_item_type(s))
    put("size", parse_size(s))
    put("schedule", parse_schedule(s))
    put("pressure_class", parse_class(s))
    mat, generic = parse_material(s)
    put("material", mat, "generic grade, ASTM spec not stated" if generic else None)
    put("end_connection", parse_end(s))
    qty, uom = parse_qty_uom(s)
    put("quantity", qty)
    put("uom", uom)
    if aliases:
        apply_aliases(s, out, aliases)
    return out, notes
