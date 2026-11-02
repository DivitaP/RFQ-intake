import { Fragment, useState } from "react";
import {
  FIELDS,
  FIELD_LABELS,
  type AgentVerdict,
  type CatalogMatch,
  type FieldName,
  type FieldValue,
  type ReviewRow,
} from "./types";

export const REVIEW_THRESHOLD = 0.8;

function band(f: FieldValue): "ok" | "warn" | "bad" | "na" {
  if (f.value === null && !f.required) return "na";
  if (f.confidence >= REVIEW_THRESHOLD) return "ok";
  if (f.confidence >= 0.5) return "warn";
  return "bad";
}

function tip(f: FieldValue): string {
  if (f.value === null && !f.required) return "not applicable";
  const lines = [`source: ${f.source}`, `confidence: ${Math.round(f.confidence * 100)}%`];
  if (f.evidence) lines.push(`from text: "${f.evidence}"`);
  if (f.source === "learned") lines.push("learned from a previous review");
  return lines.concat(f.issues.map((i) => `⚠ ${i}`)).join("\n");
}

const money = (n: number | null | undefined) => (n == null ? "" : `$${n.toFixed(2)}`);

const VERDICT_LABEL: Record<AgentVerdict["verdict"], string> = {
  equivalent: "agent: equivalent",
  upgrade: "agent: upgrade",
  not_acceptable: "agent: not acceptable",
  no_match: "agent: no match",
};

function AgentNote({ a }: { a: AgentVerdict }) {
  const trace = a.steps.map((s, i) => `${i + 1}. search_catalog(${JSON.stringify(s.args)}) → ${s.results.length} SKUs`).join("\n");
  return (
    <span className={`pill verdict ${a.verdict}`} title={`${a.reason}${trace ? `\n\n${trace}` : ""}`}>
      {VERDICT_LABEL[a.verdict]}
      {a.steps.length > 0 && ` · ${a.steps.length} search${a.steps.length > 1 ? "es" : ""}`}
    </span>
  );
}

function MatchLine({
  m,
  chosen,
  locked,
  onChoose,
}: {
  m: CatalogMatch;
  chosen: string | null;
  locked: boolean;
  onChoose: (sku: string | null) => void;
}) {
  if (m.status === "exact" || m.status === "learned") {
    return (
      <div className="match">
        <span className="pill ok">SKU</span>
        <span className="mono">{m.sku}</span>
        <span className="muted">
          {money(m.price)} · {m.stock} in stock
        </span>
        {m.status === "learned" && (
          <span className="pill learned" title={m.reason}>
            learned substitute
          </span>
        )}
      </div>
    );
  }
  const agentSku = m.agent?.sku ?? null;
  return (
    <div className="match">
      <span className={`pill ${m.candidates.length ? "warnp" : "bad"}`}>{m.candidates.length ? "no exact SKU" : "no SKU"}</span>
      {m.candidates.length === 0 && <span className="muted">{m.reason}</span>}
      {m.agent && <AgentNote a={m.agent} />}
      {m.candidates.map((c) => (
        <span key={c.sku} className={`cand ${chosen === c.sku ? "chosen" : ""} ${agentSku === c.sku ? "agent-pick" : ""}`}>
          <span className="mono">{c.sku}</span>
          {c.diffs.map((d) => (
            <span key={d.field} className="diff" title={d.note ?? ""}>
              {d.want ?? "—"} → {d.have ?? "—"}
              {d.note && " ⓘ"}
            </span>
          ))}
          <span className="muted">{money(c.price)}</span>
          {!locked && (
            <button className="use" onClick={() => onChoose(chosen === c.sku ? null : c.sku)}>
              {chosen === c.sku ? "✓ using" : "use"}
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

interface Props {
  rows: ReviewRow[];
  onEdit: (row: number, field: FieldName, value: string) => void;
  onToggle: (row: number) => void;
  onChoose: (row: number, sku: string | null) => void;
}

export default function ItemsTable({ rows, onEdit, onToggle, onChoose }: Props) {
  const [editing, setEditing] = useState<{ r: number; f: FieldName } | null>(null);

  return (
    <div className="table-wrap">
      <table className="items">
        <thead>
          <tr>
            <th className="num">#</th>
            {FIELDS.map((f) => (
              <th key={f} className={f === "quantity" ? "right" : ""}>
                {FIELD_LABELS[f]}
              </th>
            ))}
            <th className="center">Approve</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <Fragment key={row.line_no}>
              <tr className={`item ${row.approved ? "approved" : ""}`}>
                <td className="num" rowSpan={2}>
                  {row.line_no}
                </td>
                {FIELDS.map((f) => {
                  const fv = row.fields[f];
                  const isEditing = editing?.r === r && editing.f === f;
                  return (
                    <td
                      key={f}
                      className={`cell ${band(fv)} ${f === "quantity" ? "right" : ""}`}
                      title={tip(fv)}
                      onClick={() => !row.approved && setEditing({ r, f })}
                    >
                      {isEditing ? (
                        <input
                          autoFocus
                          defaultValue={fv.value ?? ""}
                          onBlur={(e) => {
                            onEdit(r, f, e.target.value);
                            setEditing(null);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                            if (e.key === "Escape") setEditing(null);
                          }}
                        />
                      ) : (
                        <span className="val">
                          {fv.value ?? (fv.required ? "—" : "")}
                          {fv.source === "human" && <span className="edited">✎</span>}
                          {fv.source === "learned" && <span className="mark learned-mark">L</span>}
                          {fv.source === "llm" && <span className="mark llm-mark">AI</span>}
                        </span>
                      )}
                    </td>
                  );
                })}
                <td className="center" rowSpan={2}>
                  <button
                    className={`approve ${row.approved ? "on" : ""}`}
                    onClick={() => onToggle(r)}
                    aria-label={row.approved ? "Unapprove" : "Approve"}
                  >
                    {row.approved ? "✓" : ""}
                  </button>
                </td>
              </tr>
              <tr className="raw-row">
                <td colSpan={FIELDS.length}>
                  {row.origin && (
                    <span className="pill origin" title={row.origin}>
                      {row.origin.startsWith("attachment: ") ? `📎 ${row.origin.slice(12)}` : "✉ body"}
                    </span>
                  )}
                  <span className="raw">{row.raw}</span>
                  {row.item_issues.map((i) => (
                    <span key={i} className="pill bad">
                      {i}
                    </span>
                  ))}
                  {row.status === "auto" && !row.approved && <span className="pill ok">auto</span>}
                  {row.match && (
                    <MatchLine m={row.match} chosen={row.chosenSku} locked={row.approved} onChoose={(sku) => onChoose(r, sku)} />
                  )}
                </td>
              </tr>
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
