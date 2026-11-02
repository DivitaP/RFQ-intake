export const FIELDS = [
  "item_type",
  "size",
  "schedule",
  "pressure_class",
  "material",
  "end_connection",
  "quantity",
  "uom",
] as const;
export type FieldName = (typeof FIELDS)[number];

export const FIELD_LABELS: Record<FieldName, string> = {
  item_type: "Type",
  size: "Size",
  schedule: "Sch",
  pressure_class: "Class",
  material: "Material",
  end_connection: "End",
  quantity: "Qty",
  uom: "UOM",
};

export const MODES = ["rules+llm", "rules", "llm-only"] as const;
export type Mode = (typeof MODES)[number];
export const MODE_LABELS: Record<Mode, string> = {
  "rules+llm": "Rules + LLM",
  rules: "Rules only",
  "llm-only": "LLM only",
};

export type RulesStore = "upstash" | "file" | "tmp";

export interface Health {
  ok: boolean;
  llm: boolean;
  model: string;
  default_mode: Mode;
  agent?: boolean;
  rules_store?: RulesStore;
}

export type Source = "rule" | "learned" | "llm" | "ocr" | "human" | "none";

export interface FieldValue {
  value: string | number | null;
  source: Source;
  confidence: number;
  required: boolean;
  issues: string[];
  evidence?: string | null;
}

export const MATCH_FIELDS = ["item_type", "size", "schedule", "pressure_class", "material", "end_connection"] as const;

export interface Diff {
  field: string;
  want: string | null;
  have: string | null;
  note: string | null;
}

export interface Candidate {
  sku: string;
  description: string;
  price: number;
  stock: number;
  diffs: Diff[];
  score: number;
}

export interface AgentVerdict {
  sku: string | null;
  verdict: "equivalent" | "upgrade" | "not_acceptable" | "no_match";
  reason: string;
  steps: { tool: string; args: Record<string, string>; results: string[] }[];
}

export interface CatalogMatch {
  status: "exact" | "learned" | "similar" | "none";
  sku: string | null;
  description: string | null;
  price: number | null;
  stock: number | null;
  candidates: Candidate[];
  reason: string;
  agent: AgentVerdict | null;
}

export interface LearnedAlias {
  text: string;
  field: string;
  value: string;
  example?: string;
  added?: string;
}

export interface Rules {
  yaml: string;
  aliases: LearnedAlias[];
  matches: { signature: string; sku: string; added?: string }[];
  store: RulesStore;
  added?: number;
  rejected?: number;
}

export interface LineItem {
  line_no: number;
  raw: string;
  fields: Record<FieldName, FieldValue>;
  status: "auto" | "review";
  item_issues: string[];
  match?: CatalogMatch | null;
  origin?: string | null; // emails: "email body" | "attachment: file.pdf"
}

export interface EmailMeta {
  subject: string;
  from: string;
  date: string;
  body: string;
  attachments: string[];
  skipped: string[];
}

export interface ExtractResult {
  doc_id: string;
  filename: string;
  kind: "text" | "scanned" | "image" | "email";
  items: LineItem[];
  warnings: string[];
  email?: EmailMeta | null;
  stats: {
    items: number;
    auto_approved: number;
    needs_review: number;
    mode?: Mode;
    ms?: number;
    llm_calls?: number;
    llm_cache_hits?: number;
    learned_fields?: number;
    catalog?: Record<string, number>;
  };
}

export interface SampleMeta {
  id: string;
  file: string;
  layout: string;
  title: string;
  n_items: number;
}

export interface EvalSlice {
  field_accuracy: number | null;
  item_recall: number | null;
  item_precision: number | null;
  auto_approved: number | null;
  auto_precision: number | null;
  docs: number;
  gt_items: number;
  llm_calls_per_doc?: number;
  tokens_per_doc?: number;
  llm_sec_per_doc?: number;
}

export interface EvalRun {
  mode: string;
  model: string | null;
  overall: EvalSlice;
  by_layout: Record<string, EvalSlice>;
  by_field: Record<string, number>;
}

export interface CurvePoint {
  doc: string;
  llm_calls: number;
  learned_fields_used: number;
  auto_approved: number;
  items: number;
  rules_added: number;
  rules_total: number;
}

/** Mode runs keyed by mode name, plus optional "_learning_curve" from `eval.py --learning-curve`. */
export type EvalRuns = Record<string, EvalRun | CurvePoint[]>;

/** Review state layered on top of the server result. */
export interface ReviewRow extends LineItem {
  approved: boolean;
  chosenSku: string | null; // reviewer-picked substitute when there's no exact SKU
}

export interface LoadedDoc {
  name: string;
  fileUrl: string;
  isPdf: boolean;
  result: ExtractResult;
}
