import { useCallback, useEffect, useMemo, useState } from "react";
import {
  downloadText,
  exportCsv,
  exportJson,
  extract,
  fileFromUrl,
  getRules,
  health,
  listSamples,
  loadEval,
  loadSample,
  sendFeedback,
} from "./api";
import EvalPanel from "./EvalPanel";
import ItemsTable, { REVIEW_THRESHOLD } from "./ItemsTable";
import { FIELDS, MATCH_FIELDS, MODES, MODE_LABELS } from "./types";
import type {
  EmailMeta,
  EvalRuns,
  FieldName,
  FieldValue,
  Health,
  LearnedAlias,
  LoadedDoc,
  Mode,
  ReviewRow,
  Rules,
  SampleMeta,
} from "./types";

const NUMERIC: FieldName[] = ["quantity"];
const STORE_NOTE: Record<string, string> = {
  upstash: "saved (persistent)",
  file: "saved to learned_rules.yaml",
  tmp: "temporary: resets when the server restarts",
};

const llmCalls = (d: LoadedDoc | null) => (d ? (d.result.stats.llm_calls ?? 0) + (d.result.stats.llm_cache_hits ?? 0) : 0);

/** What a reviewer's approval teaches the rules: LLM (or corrected) values with verbatim evidence, plus chosen SKUs. */
function feedbackFrom(rows: ReviewRow[]) {
  const aliases: LearnedAlias[] = [];
  const matches: { signature: string; sku: string }[] = [];
  for (const row of rows) {
    for (const f of FIELDS) {
      const fv = row.fields[f];
      if (f === "quantity" || !fv.evidence || fv.value === null) continue;
      if (fv.source === "llm" || fv.source === "human") {
        aliases.push({ text: fv.evidence, field: f, value: String(fv.value), example: row.raw });
      }
    }
    if (row.chosenSku) {
      matches.push({ signature: MATCH_FIELDS.map((f) => row.fields[f].value ?? "").join("|"), sku: row.chosenSku });
    }
  }
  return { aliases, matches };
}

function EmailView({ e }: { e: EmailMeta }) {
  return (
    <div className="email-view">
      <dl>
        <dt>From</dt>
        <dd>{e.from}</dd>
        <dt>Subject</dt>
        <dd>
          <strong>{e.subject}</strong>
        </dd>
        <dt>Date</dt>
        <dd>{e.date}</dd>
        {e.attachments.length > 0 && (
          <>
            <dt>Attached</dt>
            <dd>
              {e.attachments.map((a) => (
                <span key={a} className="pill">
                  📎 {a}
                </span>
              ))}
            </dd>
          </>
        )}
        {e.skipped.length > 0 && (
          <>
            <dt>Skipped</dt>
            <dd className="muted">{e.skipped.join("; ")}</dd>
          </>
        )}
      </dl>
      <pre>{e.body || "(no body text)"}</pre>
    </div>
  );
}

export default function App() {
  const [samples, setSamples] = useState<SampleMeta[]>([]);
  const [evalRuns, setEvalRuns] = useState<EvalRuns | null>(null);
  const [doc, setDoc] = useState<LoadedDoc | null>(null);
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [server, setServer] = useState<Health | null>(null);
  const [mode, setMode] = useState<Mode>("rules");
  const [rules, setRules] = useState<Rules | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 6000);
  };

  const open = useCallback((d: LoadedDoc) => {
    setDoc(d);
    setRows(d.result.items.map((it) => ({ ...it, approved: false, chosenSku: null })));
    setError(null);
  }, []);

  const openSample = useCallback(
    async (s: SampleMeta) => {
      setActiveId(s.id);
      try {
        const result = await loadSample(s);
        open({ name: s.file, fileUrl: `/samples/${s.file}`, isPdf: s.file.endsWith(".pdf"), result });
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [open],
  );

  useEffect(() => {
    listSamples().then((s) => {
      setSamples(s);
      // open on the email sample: a mix of clean and flagged rows shows the review flow immediately
      if (s.length) openSample(s.find((x) => x.layout === "email") ?? s[0]);
    });
    loadEval().then(setEvalRuns);
    health().then((h) => {
      setServer(h);
      if (h) {
        setMode(h.default_mode);
        getRules().then(setRules);
      }
    });
  }, [openSample]);

  async function upload(file: File) {
    setBusy(true);
    setActiveId(null);
    setError(null);
    try {
      const result = await extract(file, mode);
      open({ name: file.name, fileUrl: URL.createObjectURL(file), isPdf: file.type === "application/pdf", result });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function edit(r: number, f: FieldName, raw: string) {
    setRows((prev) =>
      prev.map((row, i): ReviewRow => {
        if (i !== r) return row;
        const old = row.fields[f];
        const trimmed = raw.trim();
        const value = trimmed === "" ? null : NUMERIC.includes(f) ? Number(trimmed) : trimmed.toUpperCase();
        if (value === old.value) return row;
        const updated: FieldValue = { ...old, value, source: "human", confidence: 1, issues: [] };
        return { ...row, fields: { ...row.fields, [f]: updated } };
      }),
    );
  }

  async function learn(newlyApproved: ReviewRow[]) {
    const { aliases, matches } = feedbackFrom(newlyApproved);
    if (!aliases.length && !matches.length) return;
    if (!server) {
      flash("Backend offline: approval kept, but nothing was learned.");
      return;
    }
    try {
      const res = await sendFeedback(aliases, matches);
      setRules(res);
      if (res.added) {
        const what = [...aliases.map((a) => `"${a.text}" → ${a.value}`), ...matches.map((m) => m.sku)].slice(0, 3).join(", ");
        flash(`Learned ${res.added} rule${res.added > 1 ? "s" : ""} (${what}). Next time the rules handle it without the LLM.`);
      }
    } catch (e) {
      flash((e as Error).message);
    }
  }

  const toggle = (r: number) => {
    const row = rows[r];
    if (!row.approved) learn([row]);
    setRows((prev) => prev.map((x, i) => (i === r ? { ...x, approved: !x.approved } : x)));
  };

  const choose = (r: number, sku: string | null) =>
    setRows((prev) => prev.map((x, i) => (i === r ? { ...x, chosenSku: sku } : x)));

  async function rerun() {
    if (!doc) return;
    const before = llmCalls(doc);
    setBusy(true);
    try {
      const file = await fileFromUrl(doc.fileUrl, doc.name);
      const result = await extract(file, mode);
      const next = { ...doc, result };
      open(next);
      flash(
        `Re-run (${MODE_LABELS[mode]}): LLM calls ${before} → ${llmCalls(next)}, ` +
          `${result.stats.learned_fields ?? 0} field${result.stats.learned_fields === 1 ? "" : "s"} filled by learned rules.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const isGreen = (row: ReviewRow) =>
    row.item_issues.length === 0 &&
    Object.values(row.fields).every((f) => (f.value === null && !f.required) || f.confidence >= REVIEW_THRESHOLD);

  const approveGreen = () => {
    learn(rows.filter((row) => !row.approved && isGreen(row)));
    setRows((prev) => prev.map((row) => (isGreen(row) ? { ...row, approved: true } : row)));
  };

  const counts = useMemo(() => {
    const green = rows.filter(isGreen).length;
    return { total: rows.length, green, review: rows.length - green, approved: rows.filter((r) => r.approved).length };
  }, [rows]);

  const approved = rows.filter((r) => r.approved);
  const base = doc?.name.replace(/\.[^.]+$/, "") ?? "rfq";

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>RFQ Intake</h1>
          <p className="muted">PVF RFQ → structured line items. Rules first, LLM for leftovers, a human for anything uncertain.</p>
        </div>
      </header>

      <div className="body">
        <aside className="side">
          <label
            className={`drop ${drag ? "drag" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              const f = e.dataTransfer.files[0];
              if (f) upload(f);
            }}
          >
            <input type="file" accept=".pdf,.png,.jpg,.jpeg,.eml" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <strong>{busy ? "Extracting…" : "Upload an RFQ"}</strong>
            <span className="muted">PDF, PNG, JPG or .eml · 10 MB max</span>
          </label>

          <div className="modes" role="radiogroup" aria-label="Extraction mode">
            {MODES.map((m) => {
              const needsLlm = m !== "rules";
              const off = !server || (needsLlm && !server.llm);
              return (
                <button
                  key={m}
                  role="radio"
                  aria-checked={mode === m}
                  className={mode === m ? "on" : ""}
                  disabled={off}
                  title={off ? (server ? "LLM not configured on the server" : "backend offline") : ""}
                  onClick={() => setMode(m)}
                >
                  {MODE_LABELS[m]}
                </button>
              );
            })}
          </div>
          {!server && <p className="hint muted">Backend offline: samples still work, uploads won't.</p>}

          <h3>Samples</h3>
          <ul className="samples">
            {samples.map((s) => (
              <li key={s.id}>
                <button className={activeId === s.id ? "active" : ""} onClick={() => openSample(s)}>
                  <span>{s.title}</span>
                  <span className="muted mono">
                    {s.id.slice(4, 6)} · {s.n_items} lines
                  </span>
                </button>
              </li>
            ))}
          </ul>

          {rules && (
            <div className="rules">
              <h3>
                Learned rules <span className="count">{rules.aliases.length + rules.matches.length}</span>
              </h3>
              <p className="hint muted">{STORE_NOTE[rules.store] ?? rules.store}</p>
              {rules.aliases.length + rules.matches.length === 0 ? (
                <p className="hint muted">
                  Approve a row with an <span className="mark llm-mark">AI</span> field, or pick a substitute SKU, and it becomes a
                  rule.
                </p>
              ) : (
                <ul>
                  {rules.aliases
                    .slice(-6)
                    .reverse()
                    .map((a) => (
                      <li key={`${a.text}-${a.field}`} className="mono" title={a.example}>
                        "{a.text}" → {a.field} {a.value}
                      </li>
                    ))}
                  {rules.matches
                    .slice(-3)
                    .reverse()
                    .map((m) => (
                      <li key={m.signature} className="mono" title={m.signature}>
                        substitute → {m.sku}
                      </li>
                    ))}
                </ul>
              )}
              <button className="link" onClick={() => downloadText("learned_rules.yaml", rules.yaml, "text/yaml")}>
                Download YAML
              </button>
            </div>
          )}
        </aside>

        <main className="main">
          {error && <div className="banner bad">{error}</div>}
          {doc?.result.warnings.map((w) => (
            <div key={w} className="banner warn">
              {w}
            </div>
          ))}

          {doc && (
            <div className="split">
              <section className="source">
                <div className="pane-head">
                  <span className="mono">{doc.name}</span>
                  <span>
                    <span className="pill">{doc.result.kind}</span>
                    {doc.result.stats.mode && <span className="pill">{MODE_LABELS[doc.result.stats.mode]}</span>}
                  </span>
                </div>
                {doc.result.email ? (
                  <EmailView e={doc.result.email} />
                ) : doc.isPdf ? (
                  <iframe title="source document" src={`${doc.fileUrl}#toolbar=0&view=FitH`} />
                ) : (
                  <img alt="source document" src={doc.fileUrl} />
                )}
              </section>

              <section className="extracted">
                <div className="pane-head">
                  <div className="stats">
                    <span>{counts.total} items</span>
                    <span className="muted">{llmCalls(doc)} LLM calls</span>
                    <span className="ok-t">{counts.green} clean</span>
                    <span className="bad-t">{counts.review} need review</span>
                    <span>{counts.approved} approved</span>
                  </div>
                  <div className="actions">
                    <button
                      onClick={rerun}
                      disabled={!server || busy}
                      title="Run this document again with the current mode and learned rules"
                    >
                      {busy ? "Running…" : "Re-run"}
                    </button>
                    <button onClick={approveGreen} disabled={!counts.green}>
                      Approve all clean
                    </button>
                    <button onClick={() => exportCsv(base, approved)} disabled={!approved.length}>
                      CSV
                    </button>
                    <button className="primary" onClick={() => exportJson(base, approved)} disabled={!approved.length}>
                      Export JSON
                    </button>
                  </div>
                </div>
                {rows.length ? (
                  <ItemsTable rows={rows} onEdit={edit} onToggle={toggle} onChoose={choose} />
                ) : (
                  <p className="empty muted">No line items extracted.</p>
                )}
                <p className="legend muted">
                  <span className="sw ok" /> confident <span className="sw warn" /> check <span className="sw bad" /> missing or
                  invalid · <span className="mark llm-mark">AI</span> LLM-filled · <span className="mark learned-mark">L</span>{" "}
                  learned rule · hover for source · click to edit
                </p>
              </section>
            </div>
          )}

          {toast && (
            <div className="toast" role="status" onClick={() => setToast(null)}>
              {toast}
            </div>
          )}
          <EvalPanel runs={evalRuns} />
        </main>
      </div>
    </div>
  );
}
