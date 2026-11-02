import hashlib
import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from pydantic import BaseModel

from . import llmclient, pipeline, rules_store
from .schema import ExtractResult

MAX_BYTES = 10 * 1024 * 1024
RATE_LIMIT = int(os.getenv("RFQ_RATE_LIMIT", "20"))  # uploads per IP per hour

app = FastAPI(title="RFQ Intake")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_cache: dict[str, ExtractResult] = {}
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(ip: str, limit: int = RATE_LIMIT):
    now, q = time.time(), _hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "rate limit exceeded, try again later")
    q.append(now)


@app.get("/api/health")
def health():
    return {"ok": True, "llm": llmclient.available(), "model": llmclient.describe(),
            "default_mode": pipeline.default_mode(), "agent": pipeline.agent_enabled(),
            "rules_store": rules_store.backend()}


class Feedback(BaseModel):
    aliases: list[dict] = []
    matches: list[dict] = []


def _rules_view(data: dict) -> dict:
    return {"yaml": rules_store.dump(data), "aliases": data["aliases"], "matches": data["matches"],
            "store": rules_store.backend()}


@app.get("/api/rules")
def get_rules():
    return _rules_view(rules_store.load(fresh=True))


@app.post("/api/feedback")
def feedback(request: Request, fb: Feedback):
    _rate_limit("fb:" + (request.client.host if request.client else "unknown"), limit=120)
    if len(fb.aliases) + len(fb.matches) > 50:
        raise HTTPException(413, "too much feedback in one request")
    out = rules_store.add_feedback(fb.aliases, fb.matches)
    return {"added": out["added"], "rejected": out["rejected"], **_rules_view(out["rules"])}


@app.post("/api/rules/reset")
def reset_rules(request: Request):
    token = os.getenv("RFQ_ADMIN_TOKEN")
    if not token or request.headers.get("x-admin-token") != token:
        raise HTTPException(403, "reset needs RFQ_ADMIN_TOKEN")
    rules_store.reset()
    return _rules_view(rules_store.load(fresh=True))


@app.post("/api/extract", response_model=ExtractResult)
async def extract(request: Request, file: UploadFile = File(...), mode: str | None = Query(None)):
    if mode is not None and mode not in pipeline.MODES:
        raise HTTPException(422, f"mode must be one of {pipeline.MODES}")
    if mode in ("rules+llm", "llm-only") and not llmclient.available():
        raise HTTPException(503, "LLM modes are not configured on this server")
    _rate_limit(request.client.host if request.client else "unknown")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (10 MB max)")
    rules = rules_store.load()  # cache key includes the rules version: learning invalidates old results
    key = f"{hashlib.sha256(data).hexdigest()[:16]}:{mode or pipeline.default_mode()}:{len(rules['aliases'])}:{len(rules['matches'])}"
    if key in _cache:
        return _cache[key]
    try:
        result = await run_in_threadpool(pipeline.run, data, file.filename or "upload", mode)
    except ValueError as e:
        raise HTTPException(415, str(e))
    _cache[key] = result
    return result
