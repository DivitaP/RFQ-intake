"""Schema + domain validation. Adds issues to fields; never changes values."""
from . import vocab
from .schema import FieldValue

REQUIRED = {
    "pipe": ["size", "schedule", "material", "quantity", "uom"],
    "bw": ["size", "schedule", "material", "end_connection", "quantity", "uom"],
    "forged": ["size", "pressure_class", "material", "end_connection", "quantity", "uom"],
    "flange": ["size", "pressure_class", "material", "end_connection", "quantity", "uom"],
    "valve": ["size", "pressure_class", "material", "end_connection", "quantity", "uom"],
}
ALWAYS_REQUIRED = ["item_type"]


def category(fields: dict[str, FieldValue]) -> str | None:
    t = fields["item_type"].value
    if t is None:
        return None
    if t == "pipe":
        return "pipe"
    if t in vocab.FLANGE_TYPES or t == "flange":
        return "flange"
    if t in vocab.VALVE_TYPES or t == "valve":
        return "valve"
    if t in vocab.FITTING_TYPES:
        end = fields["end_connection"].value
        cls = fields["pressure_class"].value
        if end in ("SW", "THD") or cls in vocab.FORGED_CLASSES:
            return "forged"
        return "bw"
    return None


def _issue(f: FieldValue, msg: str):
    if msg not in f.issues:
        f.issues.append(msg)


def validate(fields: dict[str, FieldValue]) -> list[str]:
    """Mutates fields (required flag + issues). Returns item-level issues."""
    item_issues: list[str] = []
    cat = category(fields)

    for name in ALWAYS_REQUIRED + REQUIRED.get(cat, []):
        fields[name].required = True
        if fields[name].value is None:
            _issue(fields[name], "missing")

    t = fields["item_type"].value
    if t in ("flange", "valve"):
        _issue(fields["item_type"], f"{t} subtype not specified")
    if cat is None:
        item_issues.append("could not determine item type")
        return item_issues

    # size
    size = fields["size"].value
    if isinstance(size, str):
        parts = size.split("x")
        if any(p not in vocab.VALID_NPS for p in parts):
            _issue(fields["size"], "not a standard NPS")
        if len(parts) == 2 and t not in ("reducer", "tee"):
            _issue(fields["size"], "two sizes on a non-reducing item")
        if cat == "forged" and _nps_to_float(parts[0]) > 4:
            _issue(fields["size"], "SW/THD fittings above 4\" are unusual")

    # schedule vs class
    sch, cls = fields["schedule"].value, fields["pressure_class"].value
    if cat in ("flange", "valve", "forged") and sch and cls is None:
        _issue(fields["schedule"], f"schedule given but {cat} items are rated by class")
    if cat in ("pipe", "bw") and cls is not None:
        _issue(fields["pressure_class"], f"class given on a {cat} item")
    if cls is not None:
        allowed = vocab.FORGED_CLASSES if cat == "forged" else vocab.FLANGE_VALVE_CLASSES
        if cat in ("flange", "valve", "forged") and cls not in allowed:
            _issue(fields["pressure_class"], f"class {cls} invalid for {cat}")

    # material vs product form
    mat = fields["material"].value
    if mat:
        spec = next((m for m in vocab.MATERIALS if m["canon"] == mat), None)
        forms = spec["forms"] if spec else None
        want = {"pipe": {"pipe"}, "bw": {"bw"}, "forged": {"forged"}, "flange": {"forged"},
                "valve": {"forged", "cast"}}[cat]
        if forms and not (forms & want):
            _issue(fields["material"], f"{mat} is a {'/'.join(sorted(forms))} spec, not valid for {cat}")

    # end connection
    end = fields["end_connection"].value
    if end:
        ok = {"pipe": {"PE", "BW", "THD"}, "bw": {"BW"}, "forged": {"SW", "THD"},
              "flange": {"RF", "FF", "RTJ"}, "valve": {"RF", "FF", "RTJ", "SW", "THD", "BW"}}[cat]
        if end not in ok:
            _issue(fields["end_connection"], f"{end} not valid for {cat}")

    # qty / uom
    q, u = fields["quantity"].value, fields["uom"].value
    if isinstance(q, (int, float)):
        if q <= 0:
            _issue(fields["quantity"], "quantity must be > 0")
        if u == "EA" and q != int(q):
            _issue(fields["quantity"], "fractional quantity for EA")
    if u and cat == "pipe" and u == "EA":
        _issue(fields["uom"], "pipe usually ordered in FT or JT")
    if u and cat != "pipe" and u in ("FT", "JT"):
        _issue(fields["uom"], f"{cat} usually ordered EA")

    return item_issues


def _nps_to_float(s: str) -> float:
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
        return 0.0
