"""Per-field confidence from evidence, not model self-reports.

Signals (base version):
  - source: rule match on clean text > LLM fill > nothing
  - validation: any issue on the field caps confidence
  - OCR: multiply by line-level OCR/agreement score when the text came from a scan
  - rules/LLM disagreement: flagged as an issue in llm.fill_missing, which caps confidence

"""
from .schema import FieldValue

REVIEW_THRESHOLD = 0.80

BASE = {"rule": 0.95, "learned": 0.95, "llm": 0.70, "ocr": 0.80, "human": 1.0, "none": 0.0}
ISSUE_CAP = 0.40
GENERIC_CAP = 0.60


def score(fields: dict[str, FieldValue], ocr_conf: float | None = None) -> None:
    for f in fields.values():
        if f.value is None:
            # not required + absent = fine (e.g. no schedule on a flange)
            f.confidence = 0.0 if f.required else 1.0
            continue
        c = BASE[f.source]
        if ocr_conf is not None and f.source != "human":
            c *= ocr_conf
        if any("generic" in i for i in f.issues):
            c = min(c, GENERIC_CAP)
        elif f.issues:
            c = min(c, ISSUE_CAP)
        f.confidence = round(c, 2)


def status(fields: dict[str, FieldValue], item_issues: list[str]) -> str:
    if item_issues:
        return "review"
    if any(f.required and f.confidence < REVIEW_THRESHOLD for f in fields.values()):
        return "review"
    if any(f.value is not None and f.confidence < REVIEW_THRESHOLD for f in fields.values()):
        return "review"
    return "auto"
