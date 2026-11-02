import { MODE_LABELS, type CurvePoint, type EvalRun, type EvalRuns, type EvalSlice, type Mode } from "./types";

const COLS: [keyof EvalSlice, string, string][] = [
  ["field_accuracy", "Field acc.", "correct fields / all ground-truth fields"],
  ["item_recall", "Item recall", "ground-truth line items found"],
  ["auto_approved", "Auto-approved", "items that skipped human review"],
  ["auto_precision", "Auto precision", "auto-approved items that were fully correct"],
];

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);
const ORDER = ["rules", "llm-only", "rules+llm"];
const label = (m: string) => MODE_LABELS[m as Mode] ?? m;

function Curve({ points }: { points: CurvePoint[] }) {
  const max = Math.max(1, ...points.map((p) => p.llm_calls));
  return (
    <div className="curve">
      <h3>Feedback loop</h3>
      <p className="muted">
        Docs processed in order; a simulated reviewer confirms correct LLM fields, which become rules. LLM calls per doc:
      </p>
      <div className="bars">
        {points.map((p) => (
          <div key={p.doc} className="bar" title={`${p.doc}: ${p.llm_calls} LLM calls, ${p.learned_fields_used} learned fields used, ${p.rules_total} rules`}>
            <span style={{ height: `${(p.llm_calls / max) * 100}%` }} />
            <em>{p.llm_calls}</em>
          </div>
        ))}
      </div>
      <p className="muted mono">
        rules learned: {points[points.length - 1]?.rules_total ?? 0} · learned fields used:{" "}
        {points.reduce((a, p) => a + p.learned_fields_used, 0)}
      </p>
    </div>
  );
}

export default function EvalPanel({ runs: all }: { runs: EvalRuns | null }) {
  if (!all) return null;
  const curve = all["_learning_curve"] as CurvePoint[] | undefined;
  const runs = Object.fromEntries(Object.entries(all).filter(([k]) => !k.startsWith("_"))) as Record<string, EvalRun>;
  const modes = Object.keys(runs).sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
  if (!modes.length) return null;
  const base = runs[modes[0]];
  const model = modes.map((m) => runs[m].model).find(Boolean);
  const layouts = Object.keys(base.by_layout).sort();

  return (
    <section className="eval">
      <header>
        <h2>Eval</h2>
        <span className="muted">
          {base.overall.docs} RFQs · {base.overall.gt_items} labeled line items{model ? ` · ${model}` : ""} ·{" "}
          <code>python eval.py --mode all</code>
        </span>
      </header>
      <table>
        <thead>
          <tr>
            <th>Slice</th>
            {modes.length > 1 && <th>Mode</th>}
            {COLS.map(([k, name, help]) => (
              <th key={k} title={help} className="right">
                {name}
              </th>
            ))}
            {modes.length > 1 && (
              <>
                <th className="right" title="LLM requests per document (both OCR passes count)">
                  LLM calls
                </th>
                <th className="right" title="summed LLM time per document">
                  LLM s
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {["overall", ...layouts].map((slice) =>
            modes.map((m, i) => {
              const s = slice === "overall" ? runs[m].overall : runs[m].by_layout[slice];
              if (!s) return null;
              return (
                <tr key={`${slice}-${m}`} className={slice === "overall" ? "strong" : ""}>
                  {i === 0 && <td rowSpan={modes.length}>{slice}</td>}
                  {modes.length > 1 && <td className="muted">{label(m)}</td>}
                  {COLS.map(([k]) => (
                    <td key={k} className="right mono">
                      {pct(s[k] as number | null)}
                    </td>
                  ))}
                  {modes.length > 1 && (
                    <>
                      <td className="right mono muted">{s.llm_calls_per_doc ?? "—"}</td>
                      <td className="right mono muted">{s.llm_sec_per_doc ?? "—"}</td>
                    </>
                  )}
                </tr>
              );
            }),
          )}
        </tbody>
      </table>
      {curve && curve.length > 0 && <Curve points={curve} />}
    </section>
  );
}
