"""Minimal multi-provider LLM client (stdlib only).

One function, generate_json(prompt, schema, system=, image=, temperature=), backed by either:
  - Gemini (native REST, schema-constrained output), or
  - any OpenAI-compatible API: Groq, OpenRouter, OpenAI (JSON mode; the schema goes in the prompt).
Output is validated downstream against the vocabulary either way, so the weaker JSON mode is safe.

Also: disk cache keyed by request hash (eval reruns are free and deterministic), request pacing
for free-tier rate limits, retries on 429/5xx, fail-fast on exhausted quota, usage metering.

Provider is picked from whichever key is set (override with LLM_PROVIDER):
    GROQ_API_KEY        -> groq        (free, no card: console.groq.com/keys)
    OPENROUTER_API_KEY  -> openrouter  (set LLM_MODEL / LLM_VISION_MODEL)
    OPENAI_API_KEY      -> openai
    GEMINI_API_KEY      -> gemini
Other env:
    LLM_MODEL, LLM_VISION_MODEL   override the provider defaults (vision model is used for images)
    LLM_RPM                       max requests/minute (default 20)
    LLM_THINKING                  Gemini only: thinking level (default low, "off" to omit)
    RFQ_LLM_CACHE                 cache file (default backend/.llm_cache.json, /tmp on Vercel)
"""
import base64
import hashlib
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_S = 45
RETRIES = 4

PROVIDERS = {
    "groq": {"key": "GROQ_API_KEY", "base": "https://api.groq.com/openai/v1",
             "model": "openai/gpt-oss-120b", "vision": "qwen/qwen3.8-27b"},
    "openrouter": {"key": "OPENROUTER_API_KEY", "base": "https://openrouter.ai/api/v1",
                   "model": "meta-llama/llama-3.3-70b-instruct", "vision": "meta-llama/llama-4-scout"},
    "openai": {"key": "OPENAI_API_KEY", "base": "https://api.openai.com/v1",
               "model": "gpt-4o-mini", "vision": "gpt-4o-mini"},
    "gemini": {"key": "GEMINI_API_KEY", "base": "https://generativelanguage.googleapis.com/v1beta",
               "model": "gemini-3.8-flash", "vision": "gemini-3.8-flash"},
}

_default_cache = "/tmp/rfq_llm_cache.json" if os.getenv("VERCEL") else str(Path(__file__).parent.parent / ".llm_cache.json")
_lock = threading.Lock()
_cache: dict | None = None

try:  # macOS python.org/conda builds often can't find system CAs for urllib -> use certifi's bundle
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

usage = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0, "ms": 0}


class LLMError(Exception):
    pass


class QuotaError(LLMError):
    """Daily/free-tier quota exhausted. Retrying is pointless; eval stops on this."""


# ---------------------------------------------------------------- config

def provider() -> str | None:
    p = os.getenv("LLM_PROVIDER")
    if p in PROVIDERS:
        return p
    for name in ("groq", "openrouter", "openai", "gemini"):
        if os.getenv(PROVIDERS[name]["key"]):
            return name
    return None


def available() -> bool:
    p = provider()
    return bool(p and os.getenv(PROVIDERS[p]["key"]))


def model(vision: bool = False) -> str:
    p = provider() or "gemini"
    legacy = os.getenv("GEMINI_MODEL") if p == "gemini" else None  # back-compat with earlier setup
    if vision:
        return os.getenv("LLM_VISION_MODEL") or legacy or PROVIDERS[p]["vision"]
    return os.getenv("LLM_MODEL") or legacy or PROVIDERS[p]["model"]


def describe() -> str:
    p = provider()
    return f"{p}:{model()}" if p else "none"


def reset_usage():
    for k in usage:
        usage[k] = 0


# ---------------------------------------------------------------- cache

def _cache_path() -> Path:
    return Path(os.getenv("RFQ_LLM_CACHE", _default_cache))


def _load():
    global _cache
    if _cache is None:
        p = _cache_path()
        try:
            _cache = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _save():
    try:
        _cache_path().write_text(json.dumps(_cache))
    except OSError:
        pass  # read-only FS: cache stays in memory


# ---------------------------------------------------------------- public API

def generate_json(prompt: str, schema: dict, *, system: str = "", image: tuple[bytes, str] | None = None,
                  temperature: float = 0.0) -> dict | list:
    """Returns parsed JSON matching `schema` (Gemini-style schema dict). image = (bytes, mime_type)."""
    p = provider()
    m = model(vision=image is not None)
    img_hash = hashlib.sha256(image[0]).hexdigest() if image else None
    key = hashlib.sha256(json.dumps([p, m, prompt, schema, system, img_hash, temperature],
                                    sort_keys=True).encode()).hexdigest()
    with _lock:
        cache = _load()
        if key in cache:
            hit = cache[key]
            usage["cache_hits"] += 1
            # replay the original call's cost so eval numbers are identical on cached reruns
            usage["input_tokens"] += hit["in"]
            usage["output_tokens"] += hit["out_tokens"]
            usage["ms"] += hit["ms"]
            return hit["out"]

    if not available():
        raise LLMError("no LLM API key set (GROQ_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY)")

    _tls.request_ms = 0
    if p == "gemini":
        out, tin, tout = _gemini(m, prompt, schema, system, image, temperature)
    else:
        out, tin, tout = _openai_compat(p, m, prompt, schema, system, image, temperature)
    ms = getattr(_tls, "request_ms", 0)

    with _lock:
        usage["calls"] += 1
        usage["ms"] += ms
        usage["input_tokens"] += tin
        usage["output_tokens"] += tout
        _load()[key] = {"out": out, "in": tin, "out_tokens": tout, "ms": ms}
        _save()
    return out


# ---------------------------------------------------------------- providers

_thinking_supported = True


def _gemini(m, prompt, schema, system, image, temperature):
    global _thinking_supported
    parts: list[dict] = []
    if image:
        parts.append({"inline_data": {"mime_type": image[1], "data": base64.b64encode(image[0]).decode()}})
    parts.append({"text": prompt})

    def build():
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                                     "responseSchema": schema}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        thinking = os.getenv("LLM_THINKING", "low")
        if thinking != "off" and _thinking_supported:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": thinking}
        return body

    url = f"{PROVIDERS['gemini']['base']}/models/{m}:generateContent"
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}
    try:
        data = _post(url, headers, build)
    except LLMError as e:
        if "thinking" in str(e).lower() and _thinking_supported:
            _log("model rejected thinkingConfig; retrying without it")
            _thinking_supported = False
            data = _post(url, headers, build)
        else:
            raise
    try:
        parts_out = data["candidates"][0]["content"]["parts"]
        text = next(x["text"] for x in parts_out if "text" in x and not x.get("thought"))
        out = json.loads(text)
    except (KeyError, IndexError, StopIteration, json.JSONDecodeError) as e:
        raise LLMError(f"bad LLM response: {e}") from e
    meta = data.get("usageMetadata", {})
    return out, meta.get("promptTokenCount", 0), meta.get("candidatesTokenCount", 0)


def _openai_compat(p, m, prompt, schema, system, image, temperature):
    # JSON mode returns an object, so array schemas are wrapped as {"items": [...]} and unwrapped below.
    is_array = schema.get("type") == "ARRAY"
    shape = {"type": "OBJECT", "properties": {"items": schema}} if is_array else schema
    instructions = (f"{prompt}\n\nRespond with ONLY a JSON object matching this schema "
                    f"(use null for unknown values; enum fields must use one of the listed values):\n"
                    f"{json.dumps(shape)}")
    content: list[dict] | str = instructions
    if image:
        url = f"data:{image[1]};base64,{base64.b64encode(image[0]).decode()}"
        content = [{"type": "text", "text": instructions}, {"type": "image_url", "image_url": {"url": url}}]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    body = {"model": m, "messages": messages, "temperature": temperature,
            "response_format": {"type": "json_object"}}
    cfg = PROVIDERS[p]
    data = _post(f"{os.getenv('LLM_BASE_URL', cfg['base'])}/chat/completions",
                 {"Authorization": f"Bearer {os.environ[cfg['key']]}"}, lambda: body)
    try:
        out = json.loads(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise LLMError(f"bad LLM response: {e}") from e
    if is_array:
        out = out.get("items", []) if isinstance(out, dict) else out
    u = data.get("usage") or {}
    return out, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


# ---------------------------------------------------------------- transport

MIN_INTERVAL_S = 60.0 / float(os.getenv("LLM_RPM", os.getenv("GEMINI_RPM", "20")))
_pace_lock = threading.Lock()
_next_slot = 0.0
_tls = threading.local()  # duration of the last successful request on this thread


def _log(msg: str):
    print(f"    [llm] {msg}", file=sys.stderr, flush=True)


def _wait_turn():
    global _next_slot
    with _pace_lock:
        now = time.time()
        start = max(now, _next_slot)
        _next_slot = start + MIN_INTERVAL_S
    if start > now:
        time.sleep(start - now)


def _post(url: str, headers: dict, build_body) -> dict:
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, data=json.dumps(build_body()).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": "rfq-intake/1.0 (+https://github.com)", **headers})
        _wait_turn()
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_SSL) as r:
                data = json.loads(r.read())
            _tls.request_ms = int((time.time() - t0) * 1000)  # excludes pacing and retry back-off
            return data
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            last = LLMError(f"HTTP {e.code}: {raw[:400]}")
            if e.code == 429 and re.search(r"exceeded your current quota|insufficient_quota|per day|tokens per day", raw, re.I):
                metrics = sorted(set(re.findall(r'"quotaMetric":\s*"([^"]+)"', raw)))
                raise QuotaError(f"quota exhausted for {describe()}"
                                 + (f" (metric: {', '.join(metrics)})" if metrics else "")) from None
            if e.code not in (429, 500, 502, 503, 504):
                break
            _log(f"HTTP {e.code}, retry {attempt + 1}/{RETRIES}")
        except (urllib.error.URLError, TimeoutError) as e:
            last = LLMError(f"unreachable: {e}")
            _log(f"{e}, retry {attempt + 1}/{RETRIES}")
        if attempt < RETRIES:
            time.sleep(min(20, 3 * 2 ** attempt))
    raise last or LLMError("LLM call failed")


# ---------------------------------------------------------------- tool calling (agent)

def supports_tools() -> bool:
    return provider() not in (None, "gemini") and available()


def chat(messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.0) -> dict:
    """One OpenAI-compatible chat turn, optionally with tools. Returns the assistant message dict.
    Cached by the full conversation so agent runs replay for free in eval and demos."""
    p = provider()
    if not supports_tools():
        raise LLMError("tool calling needs an OpenAI-compatible provider (e.g. GROQ_API_KEY)")
    m = model()
    key = hashlib.sha256(json.dumps(["chat", p, m, messages, tools, temperature], sort_keys=True).encode()).hexdigest()
    with _lock:
        hit = _load().get(key)
        if hit:
            usage["cache_hits"] += 1
            usage["input_tokens"] += hit["in"]
            usage["output_tokens"] += hit["out_tokens"]
            usage["ms"] += hit["ms"]
            return hit["out"]
    body = {"model": m, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    cfg = PROVIDERS[p]
    _tls.request_ms = 0
    data = _post(f"{os.getenv('LLM_BASE_URL', cfg['base'])}/chat/completions",
                 {"Authorization": f"Bearer {os.environ[cfg['key']]}"}, lambda: body)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"bad LLM response: {e}") from e
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = [{"id": c["id"], "type": "function",
                              "function": {"name": c["function"]["name"], "arguments": c["function"]["arguments"]}}
                             for c in msg["tool_calls"]]
    u = data.get("usage") or {}
    tin, tout, ms = u.get("prompt_tokens", 0), u.get("completion_tokens", 0), getattr(_tls, "request_ms", 0)
    with _lock:
        usage["calls"] += 1
        usage["ms"] += ms
        usage["input_tokens"] += tin
        usage["output_tokens"] += tout
        _load()[key] = {"out": out, "in": tin, "out_tokens": tout, "ms": ms}
        _save()
    return out
