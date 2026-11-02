import type { EvalRuns, ExtractResult, Health, LearnedAlias, Mode, ReviewRow, Rules, SampleMeta } from "./types";
import { FIELDS } from "./types";

const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

/** Preloaded samples are precomputed static JSON: instant, no backend, no cold start. */
export async function listSamples(): Promise<SampleMeta[]> {
  const r = await fetch("/samples/index.json");
  return r.ok ? r.json() : [];
}

export async function loadSample(s: SampleMeta): Promise<ExtractResult> {
  const r = await fetch(`/samples/${s.id}.result.json`);
  if (!r.ok) throw new Error(`could not load ${s.id}`);
  return r.json();
}

export async function loadEval(): Promise<EvalRuns | null> {
  const r = await fetch("/eval.json");
  return r.ok ? r.json() : null;
}

export async function health(): Promise<Health | null> {
  try {
    const r = await fetch(`${API}/api/health`);
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

export async function extract(file: File, mode: Mode): Promise<ExtractResult> {
  const body = new FormData();
  body.append("file", file);
  const r = await fetch(`${API}/api/extract?mode=${encodeURIComponent(mode)}`, { method: "POST", body });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail ?? `upload failed (${r.status})`);
  }
  return r.json();
}

export async function getRules(): Promise<Rules | null> {
  try {
    const r = await fetch(`${API}/api/rules`);
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

export async function sendFeedback(
  aliases: LearnedAlias[],
  matches: { signature: string; sku: string }[],
): Promise<Rules> {
  const r = await fetch(`${API}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ aliases, matches }),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail ?? `feedback failed (${r.status})`);
  }
  return r.json();
}

/** Re-fetch a document we're showing (sample or upload) as a File so it can be re-extracted. */
export async function fileFromUrl(url: string, name: string): Promise<File> {
  const blob = await (await fetch(url)).blob();
  return new File([blob], name, { type: blob.type });
}

export function downloadText(name: string, text: string, type = "text/plain") {
  download(name, text, type);
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function plain(rows: ReviewRow[]) {
  return rows.map((r) => ({
    line_no: r.line_no,
    ...Object.fromEntries(FIELDS.map((f) => [f, r.fields[f].value])),
    source_text: r.raw,
  }));
}

export function exportJson(base: string, rows: ReviewRow[]) {
  download(`${base}.items.json`, JSON.stringify(plain(rows), null, 2), "application/json");
}

export function exportCsv(base: string, rows: ReviewRow[]) {
  const data = plain(rows);
  const cols = ["line_no", ...FIELDS, "source_text"];
  const esc = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [cols.join(","), ...data.map((d) => cols.map((c) => esc((d as Record<string, unknown>)[c])).join(","))];
  download(`${base}.items.csv`, csv.join("\n"), "text/csv");
}
