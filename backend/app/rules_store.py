"""Learned rules: reviewer decisions that flow back into the deterministic layer.

Stored as one human-readable YAML document (diffable, editable, reviewable in a PR):

    version: 1
    aliases:            # text the rules didn't know -> canonical field value
      - {text: "CS FORGED", field: material, value: A105, example: "4 in blind flange ... cs forged"}
    matches:            # request signature -> SKU a reviewer accepted as the substitute
      - {signature: "elbow_90|3|STD||A234-WPB|BW", sku: EL90-3-S40-A234WPB-BW}

Where it lives (first available):
    Upstash Redis  UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN (or KV_REST_API_URL/TOKEN from Vercel)
                   -> persistent, free tier, shared by every serverless instance
    local file     backend/learned_rules.yaml (dev)       -> persistent on your machine
    /tmp file      on Vercel without Redis                -> lost on cold start (UI says so)

Safety: every entry is validated against the vocabulary / catalog before it's stored; the store is
capped so a public demo can't be flooded.
"""
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import catalog, llm, parse

KEY = "rfq:learned_rules"
MAX_ALIASES = 300
MAX_MATCHES = 300
CACHE_TTL_S = 10
ALIAS_FIELDS = {"item_type", "size", "schedule", "pressure_class", "material", "end_connection", "uom"}

_lock = threading.Lock()
_cached: tuple[float, dict] | None = None


def _redis_cfg():
    url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("KV_REST_API_URL")
    tok = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("KV_REST_API_TOKEN")
    return (url.rstrip("/"), tok) if url and tok else None


def backend() -> str:
    if _redis_cfg():
        return "upstash"
    return "tmp" if os.getenv("VERCEL") else "file"


def _file() -> Path:
    if os.getenv("VERCEL"):
        return Path("/tmp/learned_rules.yaml")
    return Path(os.getenv("RFQ_RULES_FILE", Path(__file__).parent.parent / "learned_rules.yaml"))


def _redis(cmd: list):
    url, tok = _redis_cfg()
    req = urllib.request.Request(url, data=json.dumps(cmd).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    from .llmclient import _SSL  # same certifi-backed context
    with urllib.request.urlopen(req, timeout=10, context=_SSL) as r:
        return json.loads(r.read()).get("result")


def _empty() -> dict:
    return {"version": 1, "aliases": [], "matches": []}


def _read_text() -> str:
    if _redis_cfg():
        return _redis(["GET", KEY]) or ""
    f = _file()
    return f.read_text() if f.exists() else ""


def _write_text(text: str):
    if _redis_cfg():
        _redis(["SET", KEY, text])
    else:
        _file().write_text(text)


def load(fresh: bool = False) -> dict:
    global _cached
    if not fresh and _cached and time.time() - _cached[0] < CACHE_TTL_S:
        return _cached[1]
    try:
        data = yaml.safe_load(_read_text()) or _empty()
    except Exception:  # unreadable store must never break extraction
        data = _empty()
    data.setdefault("aliases", [])
    data.setdefault("matches", [])
    _cached = (time.time(), data)
    return data


def dump(data: dict) -> str:
    header = "# Learned from reviewer decisions in RFQ Intake. Safe to edit by hand.\n"
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def save(data: dict):
    global _cached
    _write_text(dump(data))
    _cached = (time.time(), data)


def reset():
    save(_empty())


# ---------------------------------------------------------------- views used by the pipeline

def aliases() -> list[dict]:
    return load()["aliases"]


def match_map() -> dict[str, str]:
    return {m["signature"]: m["sku"] for m in load()["matches"]}


# ---------------------------------------------------------------- feedback

def _valid_alias(a: dict) -> dict | None:
    field = a.get("field")
    if field not in ALIAS_FIELDS:
        return None
    value = llm._canon(field, a.get("value"))
    text = parse.clean(str(a.get("text", "")))
    raw = parse.clean(str(a.get("example", "")))
    if value is None or not (2 <= len(text) <= 40) or not re.search(r"[A-Z]", text):
        return None
    if raw and text not in raw:
        return None
    return {"text": text, "field": field, "value": value, "example": str(a.get("example", ""))[:120]}


def add_feedback(new_aliases: list[dict], new_matches: list[dict]) -> dict:
    """Validates and merges reviewer feedback. Returns {"added": n, "rejected": n, "rules": data}."""
    by_sku, _ = catalog.load()
    added = rejected = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    with _lock:
        data = load(fresh=True)
        have_alias = {(x["text"], x["field"]) for x in data["aliases"]}
        have_match = {x["signature"] for x in data["matches"]}
        for a in new_aliases:
            v = _valid_alias(a)
            if not v or (v["text"], v["field"]) in have_alias or len(data["aliases"]) >= MAX_ALIASES:
                rejected += 1
                continue
            data["aliases"].append({**v, "added": now})
            have_alias.add((v["text"], v["field"]))
            added += 1
        for m in new_matches:
            sig, sku = str(m.get("signature", "")), m.get("sku")
            if sku not in by_sku or sig.count("|") != len(catalog.MATCH_FIELDS) - 1 or sig in have_match \
                    or len(data["matches"]) >= MAX_MATCHES:
                rejected += 1
                continue
            data["matches"].append({"signature": sig, "sku": sku, "added": now})
            have_match.add(sig)
            added += 1
        if added:
            save(data)
    return {"added": added, "rejected": rejected, "rules": data}
