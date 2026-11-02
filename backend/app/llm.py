"""LLM paths. Two uses, both constrained to the canonical vocabulary in vocab.py:

1. fill_missing (mode "rules+llm"): rules run first; only lines with missing required fields
   reach the LLM, and it may only fill fields the rules left empty. Where it disagrees with a
   rule-parsed value, that field is flagged for review (cheap audit of the rules).
2. extract_document (mode "llm-only"): the whole document goes to the LLM, no rules. This is the
   baseline the hybrid is measured against in eval.py.

Every LLM value is checked against the vocabulary; anything outside it is dropped, never trusted.
"""
import os
import re

from . import llmclient, parse, vocab
from .schema import FIELDS, FieldValue

ITEM_TYPE_ENUM = [t for t, _ in vocab.ITEM_TYPES]
SCHEDULE_ENUM = ["SCH 5S", "SCH 10", "SCH 10S", "SCH 20", "SCH 30", "SCH 40", "SCH 40S", "SCH 60", "SCH 80",
                 "SCH 80S", "SCH 100", "SCH 120", "SCH 140", "SCH 160", "STD", "XS", "XXS"]
CLASS_ENUM = sorted(vocab.FLANGE_VALVE_CLASSES | vocab.FORGED_CLASSES, key=int)
MATERIAL_ENUM = [m["canon"] for m in vocab.MATERIALS]
END_ENUM = [e for e, _ in vocab.END_CONNECTIONS]
UOM_ENUM = ["EA", "FT", "JT"]
GENERIC_MATERIALS = {m["canon"] for m in vocab.MATERIALS if m.get("generic")}

FIELD_SCHEMA = {
    "item_type": {"type": "STRING", "enum": ITEM_TYPE_ENUM, "nullable": True},
    "size": {"type": "STRING", "nullable": True,
             "description": "NPS in inches, e.g. 1/2, 3/4, 1-1/2, 2, 10. Reducers: 4x2 (large x small)."},
    "schedule": {"type": "STRING", "enum": SCHEDULE_ENUM, "nullable": True},
    "pressure_class": {"type": "STRING", "enum": CLASS_ENUM, "nullable": True},
    "material": {"type": "STRING", "enum": MATERIAL_ENUM, "nullable": True},
    "end_connection": {"type": "STRING", "enum": END_ENUM, "nullable": True},
    "quantity": {"type": "NUMBER", "nullable": True},
    "uom": {"type": "STRING", "enum": UOM_ENUM, "nullable": True},
}

SYSTEM = """You normalize industrial pipe, valve and fitting (PVF) RFQ text into structured fields.
Domain rules:
- Pipe and butt-weld (BW) fittings are rated by schedule; flanges, valves and forged socket-weld/threaded
  fittings are rated by pressure class. Forged fittings use 2000/3000/6000; flanges/valves 150-2500, 800 for small forged valves.
- Materials map to ASTM specs: forged carbon steel = A105, BW carbon fittings = A234-WPB, carbon pipe = A106-B
  (or A53-B), cast carbon valve bodies = A216-WCB, cast 316 = A351-CF8M, forged 316 = A182-F316L,
  316 pipe = A312-TP316L, 316 BW fittings = A403-WP316L, low-temp forged = A350-LF2.
  Use SS316 or CS only when the text gives just the family and the product form doesn't determine the spec.
- 'BE' (beveled end) on pipe or BW fittings = BW. Valve 'RF FLGD' = RF.
- Return null for anything the text does not state or clearly imply. Never guess quantities."""


def enabled() -> bool:
    return os.getenv("RFQ_LLM", "0") == "1" and llmclient.available()


# ---------------------------------------------------------------- canonicalization

def _canon(field: str, v):
    if v is None or v == "":
        return None
    if field == "quantity":
        try:
            q = float(v)
        except (TypeError, ValueError):
            return None
        return q if q > 0 else None
    v = str(v).strip().upper()
    if field == "size":
        parts = [p.strip().replace(" ", "-").replace('"', "") for p in re.split(r"\s*X\s*", v)]
        canon = [parse._canon_size(p) for p in parts]
        return "x".join(canon) if all(canon) and len(canon) <= 2 else None
    if field == "item_type":
        v = v.lower()
        return v if v in ITEM_TYPE_ENUM else None
    allowed = {"schedule": SCHEDULE_ENUM, "pressure_class": CLASS_ENUM, "material": MATERIAL_ENUM,
               "end_connection": END_ENUM, "uom": UOM_ENUM}[field]
    return v if v in allowed else None


# ---------------------------------------------------------------- 1. fill leftovers

def fill_missing(raw_line: str, fields: dict[str, FieldValue]) -> dict[str, FieldValue]:
    known = {f: fv.value for f, fv in fields.items() if fv.value is not None}
    prompt = (f"RFQ line:\n{raw_line}\n\nAlready parsed by rules (may be incomplete): {known}\n"
              "Return every field you can determine from the line. For each field you return, put in "
              "evidence.<field> the exact, shortest substring of the line it came from (copied verbatim).")
    schema = {"type": "OBJECT", "properties": {
        **FIELD_SCHEMA,
        "evidence": {"type": "OBJECT", "nullable": True,
                     "properties": {f: {"type": "STRING", "nullable": True} for f in FIELDS}},
    }}
    out = llmclient.generate_json(prompt, schema, system=SYSTEM)
    if not isinstance(out, dict):
        return fields
    evidence = out.get("evidence") if isinstance(out.get("evidence"), dict) else {}
    line_clean = parse.clean(raw_line)
    for f in FIELDS:
        v = _canon(f, out.get(f))
        if v is None:
            continue
        cur = fields[f]
        generic = f == "material" and cur.value in GENERIC_MATERIALS
        if cur.value is None or (generic and v not in GENERIC_MATERIALS):
            # empty, or rules only found the family and the LLM resolved the spec from product form
            ev = parse.clean(str(evidence.get(f) or ""))
            fields[f] = FieldValue(value=v, source="llm", required=cur.required,
                                   evidence=ev if ev and ev in line_clean else None)  # must be verbatim
        elif cur.source == "rule" and v != cur.value and not generic:
            cur.issues.append(f"rules and LLM disagree (LLM: {v})")
    return fields


# ---------------------------------------------------------------- 2. LLM-only baseline

DOC_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT",
              "properties": {"source_text": {"type": "STRING", "description": "the line(s) this item came from"},
                             **FIELD_SCHEMA},
              "required": ["source_text"]},
}


def extract_document(text: str | None = None, image: tuple[bytes, str] | None = None) -> list[tuple[str, dict[str, FieldValue]]]:
    prompt = ("Extract every requested line item from this RFQ. Ignore headers, greetings, notes and signatures. "
              "One array element per line item, in document order.")
    if text is not None:
        prompt += f"\n\nRFQ:\n{text}"
    out = llmclient.generate_json(prompt, DOC_SCHEMA, system=SYSTEM, image=image)
    items = []
    for row in out if isinstance(out, list) else []:
        fields = {f: FieldValue() for f in FIELDS}
        for f in FIELDS:
            v = _canon(f, row.get(f))
            if v is not None:
                fields[f] = FieldValue(value=v, source="llm")
        items.append((str(row.get("source_text", "")), fields))
    return items
