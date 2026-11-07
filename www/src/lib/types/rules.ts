/** A check in the rule language that runs on every record after a document is read (Template > Rules). */
export type RuleStatus = "suggested" | "active" | "disabled" | "broken";
export interface RuleDraft {
  scope: string;
  check: string;
  when?: string;
  severity?: "error" | "warning";
}
export interface RuleInput extends RuleDraft {
  name: string;
  description?: string;
  message?: string;
}
export interface RuleDryRun {
  records: number;
  passed: number;
  failed: number;
  not_applicable: number;
  failures: { document_id: string; name: string; rows: string[] }[];
  reads: string[];
}
export interface Rule extends RuleInput {
  id: string;
  when: string;
  severity: "error" | "warning";
  description: string;
  message: string;
  status: RuleStatus;
  created_by: string;
  created_by_kind: "person" | "agent";
  rationale: string | null;
  source_document_id: string | null;
  source_conversation_id: string | null;
  broken_reason: string | null;
  last_dry_run: RuleDryRun | null;
  created_at: string;
  updated_at: string;
}
/** Why a rule doesn't compile: which part, where (0-based columns) and how to fix it. */
export interface RuleProblem {
  code: string;
  part: "check" | "when" | "scope" | "severity";
  message: string;
  explanation: string;
  hint: string;
  col: number | null;
  end_col: number | null;
}
export interface RuleCheckResult {
  ok: boolean;
  problems: RuleProblem[];
  dry_run?: RuleDryRun;
}
export interface RuleLanguage {
  functions: { name: string; usage: string }[];
  aggregates: { name: string; usage: string }[];
  operators: string[];
  constants: string[];
  objects: { type: string; multiple: boolean; fields: { name: string; type: string; resolves_to?: string }[] }[];
}
