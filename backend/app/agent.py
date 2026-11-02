"""Bounded catalog agent: for a line with no exact SKU, the LLM searches the catalog with a tool
and decides whether a stocked item is an acceptable substitute.

Why an agent here (and only here): exact matching is deterministic and free; what needs judgment
is "is SCH 40 an acceptable stand-in for STD at 3 inch?" or "is RTJ OK for RF?" The model gets
one tool, a hard budget (MAX_TOOL_CALLS), and its answer is checked: the SKU must exist and keep
the requested item type and size. It never auto-approves: a substitution is always a human call.
"""
import json
import re

from . import catalog, llmclient
from .schema import AgentVerdict, CatalogMatch

MAX_TOOL_CALLS = 3
MAX_TURNS = 5

TOOL = {
    "type": "function",
    "function": {
        "name": "search_catalog",
        "description": "Find stocked SKUs. Every argument is an optional exact-match filter; omit one to relax it. "
                       "Returns up to 5 SKUs with their attributes.",
        "parameters": {
            "type": "object",
            "properties": {f: {"type": "string"} for f in catalog.MATCH_FIELDS},
        },
    },
}

FINAL = {
    "type": "function",
    "function": {
        "name": "final_answer",
        "description": "Submit your decision. Call this exactly once, when done.",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "chosen SKU, or empty string if none"},
                "verdict": {"type": "string", "enum": ["equivalent", "upgrade", "not_acceptable", "no_match"]},
                "reason": {"type": "string"},
            },
            "required": ["verdict", "reason"],
        },
    },
}

SYSTEM = f"""You are a pipe, valve and fitting (PVF) inside-sales engineer matching a customer request to stocked SKUs.
You have one tool, search_catalog, and may call it at most {MAX_TOOL_CALLS} times. Start from the given
candidates; search only if they don't settle it (e.g. relax one attribute at a time).
Rules:
- Never change item type or size.
- equivalent: identical in service (e.g. STD = SCH 40 wall up to NPS 10, XS = SCH 80 up to NPS 8).
- upgrade: meets or exceeds the request (e.g. A350-LF2 for A105, heavier wall); customer may need to approve.
- not_acceptable: face/end type changes (RF vs RTJ), lower rating, different material family.
- no_match: nothing suitable.
Finish by calling final_answer(sku, verdict, reason). Use only the tools search_catalog and final_answer."""


def _compact(rec: dict) -> dict:
    return {"sku": rec["sku"], **{f: rec[f] for f in catalog.MATCH_FIELDS if rec[f]}, "stock": rec["stock"]}


def _parse_final(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def resolve(raw: str, sig: dict, match: CatalogMatch) -> AgentVerdict:
    by_sku, _ = catalog.load()
    cands = [{"sku": c.sku, "differences": [{k: d[k] for k in ("field", "want", "have")} for d in c.diffs]}
             for c in match.candidates]
    user = (f"Customer line: {raw}\nParsed request: {json.dumps({k: v for k, v in sig.items() if v})}\n"
            f"Closest stocked candidates (deterministic search): {json.dumps(cands) if cands else 'none'}")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    steps: list[dict] = []
    final: dict = {}
    for _ in range(MAX_TURNS):
        tools = [TOOL, FINAL] if len(steps) < MAX_TOOL_CALLS else [FINAL]
        try:
            msg = llmclient.chat(messages, tools=tools)
        except llmclient.LLMError as e:
            if "attempted to call tool" not in str(e):
                raise
            # model invented a tool name (gpt-oss sometimes calls "json"): ask for plain-text JSON instead
            msg = llmclient.chat(messages + [{"role": "user", "content":
                                 'Reply now with ONLY JSON: {"sku": "...", "verdict": "...", "reason": "..."}'}])
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            final = _parse_final(msg["content"])
            break
        done = next((c for c in calls if c["function"]["name"] == "final_answer"), None)
        if done:
            try:
                final = json.loads(done["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                final = {}
            break
        for call in calls:
            if len(steps) >= MAX_TOOL_CALLS:
                result = {"error": "tool budget exhausted: answer now"}
            else:
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                rows = catalog.search(args)
                steps.append({"tool": "search_catalog", "args": args, "results": [r["sku"] for r in rows]})
                result = [_compact(r) for r in rows]
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
        if len(steps) >= MAX_TOOL_CALLS:
            messages.append({"role": "user", "content": "Tool budget used. Give the final JSON now."})

    sku = final.get("sku") if isinstance(final.get("sku"), str) else None
    verdict = final.get("verdict") if final.get("verdict") in ("equivalent", "upgrade", "not_acceptable", "no_match") \
        else "no_match"
    reason = str(final.get("reason") or "")[:240]
    rec = by_sku.get(sku) if sku else None
    # guardrails: SKU must exist and keep type + size
    if sku and (not rec or rec["item_type"] != sig["item_type"] or rec["size"] != sig["size"]):
        return AgentVerdict(verdict="no_match", reason=f"rejected agent pick {sku}: not a valid same-type, same-size SKU",
                            steps=steps)
    if verdict == "no_match":
        sku = None
    return AgentVerdict(sku=sku, verdict=verdict, reason=reason, steps=steps)