// ---- Analytics screens ----

export type ScreenFormat = {
  type: "text" | "number" | "currency" | "percent" | "date" | "time" | "duration";
  decimals?: number;
  currency?: string;
  align?: "left" | "right" | "center";
};
export type ScreenTone = "positive" | "warning" | "negative" | "info";
export type ScreenAggregate = "sum" | "avg" | "min" | "max" | "count" | "count_distinct";
export type ScreenFilterOp = "eq" | "ne" | "contains" | "in" | "gte" | "lte" | "between" | "empty" | "not_empty";

export interface ScreenColumn {
  id: string;
  label: string;
  kind: "field" | "formula";
  field?: string;
  formula?: string;
  scope?: "row" | "group";
  format?: ScreenFormat;
  width?: number;
  hidden?: boolean;
  aggregate?: ScreenAggregate | null;
  highlight?: { when: string; tone: ScreenTone }[];
}
export interface ScreenFilter {
  field?: string;
  formula?: string;
  op?: ScreenFilterOp;
  value?: unknown;
}
export interface ScreenDefinition {
  source: { object: string; include_pending_review?: boolean };
  columns: ScreenColumn[];
  filters?: ScreenFilter[];
  sort?: { column: string; dir: "asc" | "desc" }[];
  group_by?: string[];
  totals?: boolean;
}
export interface ScreenSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  status: "active" | "suggested";
  position: number;
  created_by: string;
  created_by_kind: "person" | "agent";
  rationale: string | null;
  object: string | null;
  columns: number;
  created_at: string;
  updated_at: string;
}
export interface Screen extends ScreenSummary {
  definition: ScreenDefinition;
}
export interface ScreenRunColumn {
  id: string;
  label: string;
  kind: "field" | "formula";
  format: ScreenFormat;
  width: number | null;
  aggregate: ScreenAggregate | null;
  formula: string | null;
}
export interface ScreenRow {
  id: string;
  document_id: string | null;
  filename: string | null;
  pending_review: boolean;
  size?: number;
  cells: Record<string, string | number | boolean | null>;
  tones: Record<string, ScreenTone>;
}
export interface ScreenRun {
  screen?: ScreenSummary;
  columns: ScreenRunColumn[];
  rows: ScreenRow[];
  totals: Record<string, string | number | null> | null;
  total_rows: number;
  offset: number;
  limit: number;
  grouped: boolean;
  documents_searched: number;
  records_truncated: boolean;
  errors: { column: string; message: string; count: number }[];
}
export interface ScreenCatalogue {
  objects: { type: string; multiple: boolean; fields: { name: string; type: string; variable: string }[] }[];
  functions: { name: string; usage: string }[];
  aggregates: ScreenAggregate[];
  filter_ops: ScreenFilterOp[];
}
export interface ScreenStarter {
  name: string;
  description: string;
  definition: ScreenDefinition;
}
