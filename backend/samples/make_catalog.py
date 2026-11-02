"""Generate a mock distributor catalog (data/catalog.csv) that overlaps the sample RFQs realistically:

  - most requested items are stocked exactly
  - ~15% are NOT stocked exactly, but a near neighbour is: the common real-world cases
      STD  requested, SCH 40 stocked  (identical wall for NPS <= 10)
      XS   requested, SCH 80 stocked  (identical wall for NPS <= 8)
      A105 requested, A350-LF2 stocked (impact-tested upgrade)
      RF   requested, RTJ stocked      (NOT interchangeable: agent should reject)
      SCH 40 requested, SCH 80 stocked (heavier wall: possible upgrade, needs sign-off)
  - plus random filler SKUs so the catalog isn't just the answer key

Usage (from backend/):  python -m samples.make_catalog
"""
import csv
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT.parent / "data" / "catalog.csv"
FIELDS = ["item_type", "size", "schedule", "pressure_class", "material", "end_connection"]
TYPE_CODE = {"elbow_90": "EL90", "elbow_45": "EL45", "tee": "TEE", "reducer": "RED", "cap": "CAP",
             "coupling": "CPL", "union": "UN", "pipe": "PIPE", "flange_wn": "WN", "flange_so": "SO",
             "flange_blind": "BL", "valve_gate": "GV", "valve_ball": "BV", "valve_check": "CV", "valve_globe": "GLV"}
SWAPS = [
    ("schedule", "STD", "SCH 40"),
    ("schedule", "XS", "SCH 80"),
    ("material", "A105", "A350-LF2"),
    ("end_connection", "RF", "RTJ"),
    ("schedule", "SCH 40", "SCH 80"),
]


def key(it):
    return tuple(it.get(f) for f in FIELDS)


def sku(it):
    parts = [TYPE_CODE[it["item_type"]], it["size"].replace("/", "").replace("-", "")]
    for f in ("schedule", "pressure_class", "material", "end_connection"):
        if it.get(f):
            parts.append(str(it[f]).replace(" ", "").replace("SCH", "S").replace("-", ""))
    return "-".join(parts)


def price(it):
    h = int(hashlib.md5(sku(it).encode()).hexdigest()[:6], 16)
    base = {"pipe": 9, "valve": 180, "flange": 35}.get(it["item_type"].split("_")[0], 14)
    return round(base * (1 + (h % 400) / 100), 2)


def describe(it):
    bits = [f'{it["size"]}"', it["item_type"].replace("_", " ").upper()]
    bits += [str(it[f]) for f in ("pressure_class", "schedule", "end_connection", "material") if it.get(f)]
    if it.get("pressure_class"):
        bits[2] = f'CL{it["pressure_class"]}'
    return " ".join(bits)


def main():
    r = random.Random(11)
    wanted = {}
    for f in sorted((ROOT / "truth").glob("*.json")):
        for it in json.loads(f.read_text())["items"]:
            wanted[key(it)] = {k: it[k] for k in FIELDS}

    stocked = {}
    swapped = 0
    for k, it in sorted(wanted.items(), key=lambda kv: str(kv[0])):
        applicable = [s for s in SWAPS if it.get(s[0]) == s[1]]
        if applicable and r.random() < 0.3:
            f, _, to = r.choice(applicable)
            alt = dict(it, **{f: to})
            stocked[key(alt)] = alt
            swapped += 1
        else:
            stocked[k] = it

    # filler: perturb stocked items (other sizes / materials) so search has distractors
    pool = list(stocked.values())
    sizes = ["1/2", "3/4", "1", "1-1/2", "2", "3", "4", "6", "8", "10", "12"]
    for _ in range(len(pool)):
        alt = dict(r.choice(pool))
        if "x" not in alt["size"]:
            alt["size"] = r.choice(sizes)
        stocked.setdefault(key(alt), alt)

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "description", *FIELDS, "list_price", "stock"])
        for it in sorted(stocked.values(), key=sku):
            w.writerow([sku(it), describe(it), *[it.get(f) or "" for f in FIELDS], price(it),
                        int(hashlib.md5(sku(it).encode()).hexdigest()[6:8], 16) % 120])
    print(f"wrote {len(stocked)} SKUs to {OUT} ({swapped} requested items stocked only as a near variant)")


if __name__ == "__main__":
    main()
