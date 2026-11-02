"""Regression eval over samples/ with ground truth.

    python eval.py                                   # rules only (no key needed)
    python eval.py --mode all --export ../frontend/public
        # rules, llm-only, rules+llm (needs an LLM key, see app/llmclient.py); writes eval.json for the UI and the
        # precomputed sample results from the best available mode (rules+llm if a key is set)
    python eval.py --min-field-acc 0.6   # exit 1 if below (CI regression gate)

Metrics
    field accuracy      correct GT fields / all GT fields (unmatched items count as all-wrong)
    item recall         GT items matched / GT items
    item precision      matched predictions / predictions
    auto-approved       % of predicted items that skipped review
    auto precision      % of auto-approved items that are fully correct  <- the safety number
"""
import argparse
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

from app import llmclient, pipeline
from app.schema import FIELDS

ROOT = Path(__file__).parent
AGENT = False  # --agent: also run the catalog agent (more LLM calls; results land in the exported samples)
SAMPLES = ROOT / "samples"


def _eq(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-6
    return a == b


def _pred_values(item) -> dict:
    return {f: item.fields[f].value for f in FIELDS}


def match(gt: list[dict], pred: list[dict]) -> list[tuple[int, int]]:
    """Greedy alignment. A pair qualifies if item_type+size agree, or >= 4 fields agree."""
    cands = []
    for i, g in enumerate(gt):
        for j, p in enumerate(pred):
            same = sum(_eq(g[f], p[f]) for f in FIELDS if g[f] is not None)
            key = _eq(g["item_type"], p["item_type"]) and _eq(g["size"], p["size"])
            if key or same >= 4:
                cands.append((same + (10 if key else 0), i, j))
    cands.sort(reverse=True)
    used_g, used_p, pairs = set(), set(), []
    for _, i, j in cands:
        if i not in used_g and j not in used_p:
            used_g.add(i)
            used_p.add(j)
            pairs.append((i, j))
    return pairs


def evaluate(mode: str, export: Path | None, write_samples: bool = True) -> dict:
    index = json.loads((SAMPLES / "index.json").read_text())
    tot = defaultdict(float)
    per_layout = defaultdict(lambda: defaultdict(float))
    per_field = defaultdict(lambda: [0, 0])
    rows = []
    for s in index:
        data = (SAMPLES / "files" / s["file"]).read_bytes()
        t_doc = time.time()
        res = pipeline.run(data, s["file"], mode, use_learned=False, use_agent=AGENT)
        if any("quota exhausted" in w for w in res.warnings):
            print(f"\n  STOPPED: {next(w for w in res.warnings if 'quota exhausted' in w)}\n"
                  "  Fix: use another provider key (e.g. GROQ_API_KEY), enable billing, or set LLM_MODEL to a model "
                  "with quota left. Cached calls are kept; rerun resumes.", flush=True)
            sys.exit(2)
        if mode != "rules":
            print(f"  [{mode}] {s['id']}: {len(res.items)} items, {res.stats['llm_calls']} calls, "
                  f"{res.stats['llm_cache_hits']} cached, {time.time() - t_doc:.0f}s"
                  + (f"  ! {res.warnings[0][:120]}" if res.warnings else ""), flush=True)
        gt = json.loads((SAMPLES / "truth" / f"{s['id']}.json").read_text())["items"]
        pred = [_pred_values(it) for it in res.items]
        pairs = match(gt, pred)
        m = {"gt_items": len(gt), "pred_items": len(pred), "matched": len(pairs),
             "gt_fields": 0, "correct_fields": 0, "auto": 0, "auto_correct": 0,
             "llm_calls": res.stats["llm_calls"] + res.stats["llm_cache_hits"],
             "llm_tokens": res.stats["llm_tokens"], "llm_ms": res.stats["llm_ms"]}
        for g in gt:
            for f in FIELDS:
                if g[f] is not None:
                    m["gt_fields"] += 1
                    per_field[f][1] += 1
        pmap = dict((j, i) for i, j in pairs)
        for i, j in pairs:
            for f in FIELDS:
                if gt[i][f] is not None and _eq(gt[i][f], pred[j][f]):
                    m["correct_fields"] += 1
                    per_field[f][0] += 1
        for j, it in enumerate(res.items):
            if it.status == "auto":
                m["auto"] += 1
                if j in pmap and all(_eq(gt[pmap[j]][f], pred[j][f]) for f in FIELDS):
                    m["auto_correct"] += 1
        for k, v in m.items():
            tot[k] += v
            per_layout[s["layout"]][k] += v
        rows.append({**s, **m, "kind": res.kind, "warnings": res.warnings, "catalog": res.stats.get("catalog", {}),
                     "agent_runs": sum(1 for it in res.items if it.match and it.match.agent)})
        if export and write_samples:
            out = export / "samples"
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy(SAMPLES / "files" / s["file"], out / s["file"])
            (out / f"{s['id']}.result.json").write_text(res.model_dump_json())

    def summarize(t):
        return {
            "field_accuracy": _r(t["correct_fields"], t["gt_fields"]),
            "item_recall": _r(t["matched"], t["gt_items"]),
            "item_precision": _r(t["matched"], t["pred_items"]),
            "auto_approved": _r(t["auto"], t["pred_items"]),
            "auto_precision": _r(t["auto_correct"], t["auto"]),
            "docs": None, "gt_items": int(t["gt_items"]),
            "_llm": (t["llm_calls"], t["llm_tokens"], t["llm_ms"]),
        }

    def with_docs(summary, n):
        calls, tokens, ms = summary.pop("_llm")
        return summary | {"docs": n, "llm_calls_per_doc": round(calls / n, 1), "tokens_per_doc": round(tokens / n),
                          "llm_sec_per_doc": round(ms / n / 1000, 1)}

    cat = {k: sum(d["catalog"].get(k, 0) for d in rows) for k in ("exact", "learned", "similar", "none")}
    agent_runs = sum(d["agent_runs"] for d in rows)
    report = {
        "catalog": cat, "agent_runs": agent_runs,
        "mode": mode,
        "model": llmclient.describe() if mode != "rules" else None,
        "overall": with_docs(summarize(tot), len(index)),
        "by_layout": {k: with_docs(summarize(v), sum(1 for s in index if s["layout"] == k))
                      for k, v in per_layout.items()},
        "by_field": {f: _r(c, n) for f, (c, n) in per_field.items()},
        "docs": rows,
    }
    if export:
        (export / "samples").mkdir(parents=True, exist_ok=True)
        (export / "samples" / "index.json").write_text(json.dumps(index, indent=1))
        prev = export / "eval.json"
        runs = json.loads(prev.read_text()) if prev.exists() else {}
        if AGENT:
            # agent calls would inflate the extraction cost: keep the mode's extraction row, record the agent apart
            runs["_agent"] = {"mode": mode, "catalog": report["catalog"], "agent_runs": report["agent_runs"],
                              "llm_calls_per_doc_incl_agent": report["overall"]["llm_calls_per_doc"]}
        else:
            runs[mode] = {k: v for k, v in report.items() if k != "docs"}
        prev.write_text(json.dumps(runs, indent=1))
    return report


def learning_curve(export: Path | None) -> list[dict]:
    """Prequential simulation of the feedback loop: process docs in order with rules+llm; after each doc a
    simulated reviewer confirms LLM-filled fields that match ground truth (with verbatim evidence), which
    become learned aliases. Learning from doc i only affects docs after i, so nothing is tested on itself."""
    import os
    import tempfile
    from app import rules_store
    tmp = Path(tempfile.mkdtemp()) / "curve_rules.yaml"
    os.environ["RFQ_RULES_FILE"] = str(tmp)
    rules_store._cached = None
    index = [s for s in json.loads((SAMPLES / "index.json").read_text()) if s["layout"] in ("email",)]
    index += [s for s in json.loads((SAMPLES / "index.json").read_text()) if s["layout"] not in ("email",)]
    out = []
    for s in index:
        data = (SAMPLES / "files" / s["file"]).read_bytes()
        res = pipeline.run(data, s["file"], "rules+llm", use_learned=True, use_agent=False)
        gt = json.loads((SAMPLES / "truth" / f"{s['id']}.json").read_text())["items"]
        pred = [_pred_values(it) for it in res.items]
        confirmed = []
        for i, j in match(gt, pred):
            for f, fv in res.items[j].fields.items():
                if fv.source == "llm" and fv.evidence and _eq(fv.value, gt[i][f]):
                    confirmed.append({"text": fv.evidence, "field": f, "value": fv.value, "example": res.items[j].raw})
        added = rules_store.add_feedback(confirmed, [])["added"] if confirmed else 0
        row = {"doc": s["id"], "llm_calls": res.stats["llm_calls"] + res.stats["llm_cache_hits"],
               "learned_fields_used": res.stats["learned_fields"], "auto_approved": res.stats["auto_approved"],
               "items": res.stats["items"], "rules_added": added,
               "rules_total": len(rules_store.load(fresh=True)["aliases"])}
        out.append(row)
        print(f"  {s['id']:<16} llm calls {row['llm_calls']:>2}  learned fields used {row['learned_fields_used']:>2}  "
              f"auto {row['auto_approved']:>2}/{row['items']:<2}  +{added} rules (total {row['rules_total']})", flush=True)
    del os.environ["RFQ_RULES_FILE"]
    if export:
        p = export / "eval.json"
        runs = json.loads(p.read_text()) if p.exists() else {}
        runs["_learning_curve"] = out
        p.write_text(json.dumps(runs, indent=1))
    return out


def _r(a, b):
    return round(a / b, 3) if b else None


def print_warnings(r):
    seen = {}
    for d in r["docs"]:
        for w in d["warnings"]:
            seen.setdefault(w, []).append(d["id"])
    for w, ids in seen.items():
        print(f"  ! {w[:220]}  ({len(ids)} docs)")
    if r["model"] and r["overall"]["llm_calls_per_doc"] == 0:
        print("  ! no LLM calls succeeded in this mode: the numbers above are NOT LLM results")


def print_report(r):
    cols = ["field_accuracy", "item_recall", "item_precision", "auto_approved", "auto_precision"]
    o = r["overall"]
    print(f"\nmode: {r['mode']}" + (f"  ({r['model']}: {o['llm_calls_per_doc']} calls, {o['tokens_per_doc']} tokens, "
                                     f"{o['llm_sec_per_doc']}s LLM time per doc)" if r["model"] else "") + "\n")
    print(f"{'slice':<10}{'docs':>5}{'items':>7}  " + "".join(f"{c:>16}" for c in cols))
    for name, s in [("OVERALL", r["overall"])] + sorted(r["by_layout"].items()):
        print(f"{name:<10}{s['docs']:>5}{s['gt_items']:>7}  " + "".join(
            f"{('-' if s[c] is None else f'{s[c]:.1%}'):>16}" for c in cols))
    c = r["catalog"]
    print(f"\ncatalog: {c['exact']} exact SKU, {c['similar']} similar (needs reviewer), {c['none']} no match"
          + (f"; agent reviewed {r['agent_runs']}" if r["agent_runs"] else ""))
    print("per-field accuracy: " + ", ".join(f"{f}={v:.0%}" for f, v in r["by_field"].items()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=[*pipeline.MODES, "all"], default="rules")
    ap.add_argument("--export", type=Path)
    ap.add_argument("--min-field-acc", type=float)
    ap.add_argument("--agent", action="store_true", help="run the catalog agent on unmatched lines")
    ap.add_argument("--learning-curve", action="store_true", help="simulate the reviewer feedback loop (needs a key)")
    a = ap.parse_args()
    AGENT = a.agent
    if a.learning_curve:
        print("learning curve (rules+llm, simulated reviewer, emails first):")
        learning_curve(a.export)
        sys.exit(0)
    if a.mode == "all":
        modes = ["rules", "llm-only", "rules+llm"] if llmclient.available() else ["rules"]
        if not llmclient.available():
            print("no LLM API key set: running rules only")
        if a.export and (a.export / "eval.json").exists():
            (a.export / "eval.json").unlink()  # fresh comparison
    else:
        modes = [a.mode]
    for i, m in enumerate(modes):
        rep = evaluate(m, a.export, write_samples=(i == len(modes) - 1))
        print_report(rep)
        print_warnings(rep)
    if a.min_field_acc and rep["overall"]["field_accuracy"] < a.min_field_acc:
        sys.exit(1)