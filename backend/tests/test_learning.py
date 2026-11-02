"""Catalog matching, the learning loop (feedback -> YAML -> rules), and the agent's guardrails."""
import json

import pytest

from app import agent, catalog, llmclient, parse, pipeline, rules_store, validate
from app.schema import CatalogMatch


@pytest.fixture(autouse=True)
def tmp_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("RFQ_RULES_FILE", str(tmp_path / "rules.yaml"))
    for k in ("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL", "VERCEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(rules_store, "_cached", None)
    yield


def _fields(line, aliases=None):
    f, _ = parse.parse_line(line, aliases)
    validate.validate(f)
    return f


# ---------------------------------------------------------------- catalog

def test_exact_match():
    m = catalog.match(_fields('4 EA 6" WN FLG 150# RF A105'))
    assert m.status == "exact" and m.sku == "WN-6-150-A105-RF"


def test_similar_match_explains_equivalence():
    m = catalog.match(_fields('2 EA 3" 90 ELL STD BW A234 WPB'))
    assert m.status == "similar"
    d = m.candidates[0].diffs[0]
    assert (d["want"], d["have"]) == ("STD", "SCH 40") and "same wall" in d["note"]


def test_never_suggests_another_size():
    m = catalog.match(_fields('2 EA 3" 90 ELL STD BW A234 WPB'))
    assert all(c.sku.split("-")[1] == "3" for c in m.candidates)


# ---------------------------------------------------------------- learning loop

def test_feedback_alias_then_rules_handle_it_without_llm():
    line = "4 in blind flange 150 lb rtj cs forged - 2 each"
    before = _fields(line)
    assert before["material"].value == "CS"  # generic family only -> would go to LLM / review
    out = rules_store.add_feedback([{"text": "cs forged", "field": "material", "value": "A105", "example": line}], [])
    assert out["added"] == 1
    after = _fields(line, rules_store.aliases())
    assert after["material"].value == "A105" and after["material"].source == "learned"
    assert not pipeline.needs_llm(after)


def test_feedback_is_validated():
    line = "50 EA something"
    out = rules_store.add_feedback([
        {"text": "x", "field": "material", "value": "A105", "example": line},              # too short
        {"text": "SOMETHING", "field": "material", "value": "UNOBTAINIUM", "example": line},  # off-vocab
        {"text": "NOT IN LINE", "field": "material", "value": "A105", "example": line},       # not in source
        {"text": "SOMETHING", "field": "quantity", "value": "5", "example": line},            # field not learnable
    ], [{"signature": "a|b", "sku": "NOPE"}])
    assert out["added"] == 0 and out["rejected"] == 5


def test_accepted_substitute_becomes_learned_match():
    f = _fields('2 EA 3" 90 ELL STD BW A234 WPB')
    m = catalog.match(f)
    sku = m.candidates[0].sku
    rules_store.add_feedback([], [{"signature": catalog.sig_key(catalog.signature(f)), "sku": sku}])
    m2 = catalog.match(f, rules_store.match_map())
    assert m2.status == "learned" and m2.sku == sku


def test_rules_file_is_readable_yaml():
    rules_store.add_feedback([{"text": "6K", "field": "pressure_class", "value": "6000", "example": "6K tee"}], [])
    text = rules_store.dump(rules_store.load(fresh=True))
    assert "aliases:" in text and "6K" in text


# ---------------------------------------------------------------- agent

def _script(monkeypatch, replies):
    """Fake chat: returns scripted assistant messages in order."""
    it = iter(replies)
    monkeypatch.setattr(llmclient, "chat", lambda messages, tools=None, temperature=0.0: next(it))


def _call(args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "search_catalog", "arguments": json.dumps(args)}}]}


def test_agent_searches_then_answers(monkeypatch):
    f = _fields('2 EA 3" 90 ELL STD BW A234 WPB')
    m = catalog.match(f)
    _script(monkeypatch, [_call({"item_type": "elbow_90", "size": "3", "material": "A234-WPB"}),
                          {"role": "assistant", "content": json.dumps(
                              {"sku": m.candidates[0].sku, "verdict": "equivalent", "reason": "STD = SCH 40 at NPS 3"})}])
    v = agent.resolve("raw", catalog.signature(f), m)
    assert v.verdict == "equivalent" and v.sku == m.candidates[0].sku and len(v.steps) == 1


def test_agent_cannot_change_size(monkeypatch):
    f = _fields('2 EA 3" 90 ELL STD BW A234 WPB')
    other = next(s for s in catalog.load()[0] if s.startswith("EL90-") and not s.startswith("EL90-3-"))
    _script(monkeypatch, [{"role": "assistant", "content": json.dumps({"sku": other, "verdict": "equivalent", "reason": "x"})}])
    v = agent.resolve("raw", catalog.signature(f), catalog.match(f))
    assert v.sku is None and v.verdict == "no_match"


def test_agent_tool_budget(monkeypatch):
    f = _fields('2 EA 3" 90 ELL STD BW A234 WPB')
    _script(monkeypatch, [_call({"size": "3"})] * 3 + [{"role": "assistant", "content": '{"sku": null, "verdict": "no_match", "reason": "none"}'}])
    v = agent.resolve("raw", catalog.signature(f), CatalogMatch(status="none"))
    assert len(v.steps) == agent.MAX_TOOL_CALLS


def test_agent_final_answer_tool(monkeypatch):
    f = _fields('2 EA 3" 90 ELL STD BW A234 WPB')
    m = catalog.match(f)
    done = {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "type": "function", "function": {
        "name": "final_answer", "arguments": json.dumps({"sku": m.candidates[0].sku, "verdict": "equivalent", "reason": "r"})}}]}
    _script(monkeypatch, [done])
    v = agent.resolve("raw", catalog.signature(f), m)
    assert v.verdict == "equivalent" and v.sku == m.candidates[0].sku


def test_agent_recovers_from_invented_tool(monkeypatch):
    f = _fields('2 EA 3" 90 ELL STD BW A234 WPB')
    m = catalog.match(f)
    replies = iter([llmclient.LLMError("HTTP 400: attempted to call tool 'json' which was not in request.tools"),
                    {"role": "assistant", "content": json.dumps({"sku": m.candidates[0].sku, "verdict": "upgrade", "reason": "r"})}])

    def fake(messages, tools=None, temperature=0.0):
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(llmclient, "chat", fake)
    v = agent.resolve("raw", catalog.signature(f), m)
    assert v.verdict == "upgrade"