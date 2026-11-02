"""ingest -> extract -> segment -> rules -> (LLM leftovers) -> validate -> confidence -> status

Modes (compared head-to-head in eval.py):
    rules       deterministic only, no LLM calls
    rules+llm   rules first; LLM fills only what rules left empty (default when a key is set)
    llm-only    whole document to the LLM, no rules; same validation and scoring afterwards
"""
import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from . import agent, catalog, confidence, email_ingest, extract, llmclient, llm, ocr, parse, rules_store, segment, validate
from .schema import ExtractResult, FieldValue, LineItem

Mode = Literal["rules", "rules+llm", "llm-only"]
MODES: tuple[str, ...] = ("rules", "rules+llm", "llm-only")
LLM_CONCURRENCY = 4
MAX_AGENT_ITEMS = 6  # per document: bounds cost and latency


def agent_enabled() -> bool:
    return os.getenv("RFQ_AGENT", "1") == "1" and llmclient.supports_tools()


def default_mode() -> Mode:
    return "rules+llm" if llm.enabled() else "rules"


def needs_llm(fields) -> bool:
    return any(f.required and f.value is None for f in fields.values()) or any(
        f.value in llm.GENERIC_MATERIALS for f in fields.values())


def _finish(i: int, raw: str, fields: dict[str, FieldValue], ocr_conf: float | None) -> LineItem:
    for f in fields.values():
        f.issues = [x for x in f.issues if x != "missing"]
    item_issues = validate.validate(fields)
    confidence.score(fields, ocr_conf)
    return LineItem(line_no=i, raw=raw, fields=fields, item_issues=item_issues,
                    status=confidence.status(fields, item_issues))


def process_lines(lines: list[tuple[str, float | None]], use_llm: bool,
                  aliases: list[dict] | None = None) -> tuple[list[LineItem], list[str]]:
    warnings: list[str] = []
    parsed = []
    for raw, src in segment.segment_spans([t for t, _ in lines]):
        confs = [lines[k][1] for k in src if lines[k][1] is not None]
        fields, _ = parse.parse_line(raw, aliases)
        validate.validate(fields)  # sets `required` so needs_llm knows what's missing
        parsed.append([raw, fields, min(confs) if confs else None])

    todo = [p for p in parsed if use_llm and needs_llm(p[1])]
    if todo:
        def fill(p):
            try:
                p[1] = llm.fill_missing(p[0], p[1])
            except llmclient.LLMError as e:
                return str(e)
            return None
        with ThreadPoolExecutor(LLM_CONCURRENCY) as ex:
            errors = {e for e in ex.map(fill, todo) if e}
        warnings += [f"LLM fallback unavailable: {e}" for e in sorted(errors)]

    items = [_finish(i, raw, fields, conf) for i, (raw, fields, conf) in enumerate(parsed, start=1)]
    return items, warnings


def attach_matches(items: list[LineItem], learned_matches: dict, use_agent: bool) -> list[str]:
    """Catalog lookup for every item; the agent only for lines with no exact/learned SKU."""
    for it in items:
        it.match = catalog.match(it.fields, learned_matches)
    if not use_agent:
        return []
    todo = [it for it in items if it.match.status in ("similar", "none")
            and it.fields["item_type"].value and it.fields["size"].value][:MAX_AGENT_ITEMS]

    def go(it):
        try:
            it.match.agent = agent.resolve(it.raw, catalog.signature(it.fields), it.match)
        except llmclient.LLMError as e:
            return str(e)
        return None
    with ThreadPoolExecutor(LLM_CONCURRENCY) as ex:
        errors = {e for e in ex.map(go, todo) if e}
    return [f"catalog agent unavailable: {e}" for e in sorted(errors)]


class ScanSkipped(Exception):
    pass


def _part_lines(part: email_ingest.Part, mode: str) -> tuple[list[tuple[str, float | None]], str]:
    """Raw lines (with OCR confidence for scans) and the part's kind: text | scanned | image."""
    if part.ftype == "text":
        return [(l, None) for l in part.text.splitlines()], "text"  # keep blanks: they end a line item
    if part.ftype == "pdf":
        text_lines, scanned = extract.pdf_lines(part.data)
        if not scanned:
            return [(l, None) for l in text_lines], "text"
        kind = "scanned"
    else:
        kind = "image"
    if mode == "rules":
        # rules mode is the no-LLM baseline: scans need vision transcription, so they're skipped
        raise ScanSkipped("scanned document: rules-only mode makes no LLM calls (use rules+llm)")
    return list(ocr.extract_lines(part.data, part.ftype)), kind


def _llm_only_items(part: email_ingest.Part) -> list[LineItem]:
    if part.ftype == "text":
        rows = llm.extract_document(text=part.text)
    else:
        text_lines, scanned = extract.pdf_lines(part.data) if part.ftype == "pdf" else ([], True)
        if not scanned:
            rows = llm.extract_document(text="\n".join(text_lines))
        else:
            rows = []
            for img in ocr.page_images(part.data, part.ftype):
                rows += llm.extract_document(image=img)
    items = []
    for i, (raw, fields) in enumerate(rows, start=1):
        validate.validate(fields)
        items.append(_finish(i, raw, fields, None))
    return items


def run(data: bytes, filename: str, mode: Mode | None = None, use_learned: bool = True,
        use_agent: bool | None = None) -> ExtractResult:
    """use_learned=False for eval: learned rules come from reviewing these same documents (train-on-test)."""
    mode = mode or default_mode()
    use_agent = (agent_enabled() and mode != "rules") if use_agent is None else use_agent
    learned_aliases, learned_matches = ([], {})
    if use_learned:
        try:
            learned_aliases, learned_matches = rules_store.aliases(), rules_store.match_map()
        except Exception:  # a broken rules store must never break extraction
            pass
    llmclient.reset_usage()
    t0 = time.time()
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    ftype = extract.sniff(data, filename)
    warnings: list[str] = []
    email_meta = None

    if ftype == "email":
        pe = email_ingest.parse(data)
        parts, kind = pe.parts, "email"
        warnings += [f"skipped {s}" for s in pe.skipped]
        email_meta = {"subject": pe.subject, "from": pe.sender, "date": pe.date, "body": pe.body[:4000],
                      "attachments": [p.name.removeprefix("attachment: ") for p in pe.parts if p.ftype != "text"],
                      "skipped": pe.skipped}
        if not parts:
            warnings.append("email has no body text or supported attachments")
    else:
        parts, kind = [email_ingest.Part(name=filename, ftype=ftype, data=data)], None

    items: list[LineItem] = []
    for part in parts:
        label = part.name if ftype == "email" else None
        try:
            if mode == "llm-only":
                part_items = _llm_only_items(part)
                if kind is None:
                    kind = "text" if part.ftype == "pdf" and not extract.pdf_lines(part.data)[1] else (
                        "scanned" if part.ftype == "pdf" else "image")
            else:
                lines, part_kind = _part_lines(part, mode)
                kind = kind or part_kind
                part_items, w = process_lines(lines, use_llm=(mode == "rules+llm"), aliases=learned_aliases)
                warnings += [f"{label}: {x}" if label else x for x in w]
        except (ScanSkipped, ocr.ScanNotSupported, llmclient.LLMError) as e:
            warnings.append(f"{label}: {e}" if label else str(e))
            kind = kind or ("scanned" if part.ftype == "pdf" else "image")
            continue
        for it in part_items:
            it.origin = label
        items += part_items

    for i, it in enumerate(items, start=1):
        it.line_no = i
    warnings += attach_matches(items, learned_matches, use_agent)
    if not items and not warnings:
        warnings.append("no line items detected")
    return ExtractResult(doc_id=doc_id, filename=filename, kind=kind or "text", items=items, warnings=warnings,
                         stats=_stats(items, mode, t0), email=email_meta)


def _stats(items: list[LineItem], mode: str, t0: float) -> dict:
    n = len(items)
    auto = sum(1 for it in items if it.status == "auto")
    matched = {k: sum(1 for it in items if it.match and it.match.status == k) for k in ("exact", "learned", "similar", "none")}
    learned_fields = sum(1 for it in items for f in it.fields.values() if f.source == "learned")
    return {"items": n, "auto_approved": auto, "needs_review": n - auto, "mode": mode,
            "catalog": matched, "learned_fields": learned_fields,
            "ms": int((time.time() - t0) * 1000), "llm_calls": llmclient.usage["calls"],
            "llm_cache_hits": llmclient.usage["cache_hits"], "llm_ms": llmclient.usage["ms"],
            "llm_tokens": llmclient.usage["input_tokens"] + llmclient.usage["output_tokens"]}
