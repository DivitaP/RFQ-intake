"""Catalog matching: exact -> learned (reviewer-accepted) -> similar candidates with explicit diffs.

Similar candidates must share item type and size (a different size is never a substitute).
Known engineering equivalences are annotated so the reviewer (and the agent) see *why* a
candidate is close, e.g. STD == SCH 40 wall for NPS <= 10.
"""
import csv
from functools import lru_cache
from pathlib import Path

from .schema import CatalogMatch, Candidate, FieldValue

CATALOG = Path(__file__).parent.parent / "data" / "catalog.csv"
MATCH_FIELDS = ["item_type", "size", "schedule", "pressure_class", "material", "end_connection"]
WEIGHTS = {"schedule": 2, "pressure_class": 3, "material": 3, "end_connection": 2}
MAX_SIMILAR_SCORE = 4
MAX_CANDIDATES = 3


def _nps(size: str) -> float:
    s = size.split("x")[0]
    try:
        if "-" in s:
            w, fr = s.split("-")
            n, d = fr.split("/")
            return int(w) + int(n) / int(d)
        if "/" in s:
            n, d = s.split("/")
            return int(n) / int(d)
        return float(s)
    except ValueError:
        return 99.0


def equivalence(field: str, want, have, size: str) -> str | None:
    """Short engineering note when two values are equivalent or a recognised upgrade."""
    pair = {want, have}
    if field == "schedule":
        if pair == {"STD", "SCH 40"} and _nps(size) <= 10:
            return "STD and SCH 40 are the same wall thickness up to NPS 10"
        if pair == {"XS", "SCH 80"} and _nps(size) <= 8:
            return "XS and SCH 80 are the same wall thickness up to NPS 8"
        if want in ("SCH 40", "STD") and have in ("SCH 80", "XS"):
            return "heavier wall than requested: possible upgrade, needs engineering sign-off"
    if field == "material" and want == "A105" and have == "A350-LF2":
        return "A350-LF2 is impact-tested carbon steel: common acceptable upgrade for A105"
    if field == "end_connection" and pair == {"RF", "RTJ"}:
        return "RF and RTJ faces are not interchangeable"
    return None


@lru_cache(maxsize=1)
def load() -> tuple[dict, dict]:
    """Returns (by_sku, by_type_size) with rows as dicts of canonical values."""
    by_sku, by_ts = {}, {}
    with CATALOG.open() as fh:
        for row in csv.DictReader(fh):
            item = {f: (row[f] or None) for f in MATCH_FIELDS}
            rec = {"sku": row["sku"], "description": row["description"], "price": float(row["list_price"]),
                   "stock": int(row["stock"]), **item}
            by_sku[rec["sku"]] = rec
            by_ts.setdefault((item["item_type"], item["size"]), []).append(rec)
    return by_sku, by_ts


def signature(fields: dict[str, FieldValue]) -> dict:
    return {f: fields[f].value for f in MATCH_FIELDS}


def sig_key(sig: dict) -> str:
    return "|".join(str(sig.get(f) or "") for f in MATCH_FIELDS)


def _candidate(rec: dict, sig: dict) -> Candidate:
    diffs = []
    for f in MATCH_FIELDS[2:]:
        if (sig.get(f) or None) != rec[f]:
            diffs.append({"field": f, "want": sig.get(f), "have": rec[f],
                          "note": equivalence(f, sig.get(f), rec[f], sig["size"] or "")})
    score = sum(WEIGHTS[d["field"]] for d in diffs)
    return Candidate(sku=rec["sku"], description=rec["description"], price=rec["price"], stock=rec["stock"],
                     diffs=diffs, score=score)


def search(filters: dict, limit: int = 5) -> list[dict]:
    """Tool for the agent: SKUs matching every given (non-null) filter."""
    by_sku, _ = load()
    want = {k: v for k, v in filters.items() if k in MATCH_FIELDS and v not in (None, "")}
    out = [r for r in by_sku.values() if all(str(r[k]) == str(v) for k, v in want.items())]
    return out[:limit]


def match(fields: dict[str, FieldValue], learned_matches: dict[str, str] | None = None) -> CatalogMatch:
    by_sku, by_ts = load()
    sig = signature(fields)
    if not sig["item_type"] or not sig["size"]:
        return CatalogMatch(status="none", reason="item type or size unknown")

    rows = by_ts.get((sig["item_type"], sig["size"]), [])
    for rec in rows:
        if all((sig[f] or None) == rec[f] for f in MATCH_FIELDS):
            return CatalogMatch(status="exact", sku=rec["sku"], description=rec["description"],
                                price=rec["price"], stock=rec["stock"])

    learned_sku = (learned_matches or {}).get(sig_key(sig))
    if learned_sku in by_sku:
        rec = by_sku[learned_sku]
        c = _candidate(rec, sig)
        return CatalogMatch(status="learned", sku=rec["sku"], description=rec["description"], price=rec["price"],
                            stock=rec["stock"], candidates=[c],
                            reason="reviewer previously accepted this substitute for the same request")

    cands = sorted((_candidate(r, sig) for r in rows), key=lambda c: c.score)
    cands = [c for c in cands if c.score <= MAX_SIMILAR_SCORE][:MAX_CANDIDATES]
    if cands:
        return CatalogMatch(status="similar", candidates=cands)
    return CatalogMatch(status="none", reason="no stocked item with this type and size")
