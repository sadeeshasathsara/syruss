// ---------------------------------------------------------------------------
// Workspace settings, access, billing, integrations
// ---------------------------------------------------------------------------

/** A role id: a built-in one ("owner", "admin", "editor", "reviewer", "viewer") or the id of a custom role. */
export type Role = string;
export type Permission =
  | "documents.view" | "documents.upload" | "documents.review" | "screens.manage" | "knowledge.view" | "knowledge.manage" | "template.publish"
  | "agent.chat" | "members.view" | "members.manage" | "roles.manage" | "integrations.manage" | "audit.view"
  | "workspace.manage" | "billing.manage" | "workspace.delete";
export type OrgRole = "owner" | "admin" | "member";
export type OrgPermission = "org.manage" | "org.members.manage" | "workspaces.create" | "workspaces.delete" | "billing.manage" | "org.audit.view";

export interface Access {
  role: Role;
  role_label: string;
  permissions: Permission[];
}

export interface WorkspaceSettingsData {
  general: { timezone: string; date_format: "YYYY-MM-DD" | "DD/MM/YYYY" | "MM/DD/YYYY"; language: string };
  agent: {
    instructions: string;
    may_register_entities: boolean;
    may_learn_aliases: boolean;
    reflection: "off" | "daily" | "weekly";
    insights?: "off" | "weekly";
    pages_per_chat_request: number;
    pages_per_person_per_day: number;
    past_pages_per_run: number;
  };
  documents: { auto_process: boolean; max_file_mb: number; allowed_types: string[]; commit_threshold: number };
  budget: { monthly_cost_usd: number | null; monthly_tokens: number | null; when_reached: "warn" | "pause" };
}

export interface BudgetStatus {
  limits: WorkspaceSettingsData["budget"];
  used: { cost_usd: number; tokens: number; runs_without_price: number };
  share: number | null;
  state: "unlimited" | "ok" | "warning" | "reached";
  paused: boolean;
  period_start: string;
  resets_at: string;
}

export interface WorkspaceJobInfo {
  job: string;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: string | null;
  state: Record<string, unknown>;
}

export interface ReflectionState {
  cadence: WorkspaceSettingsData["agent"]["reflection"];
  reflect: WorkspaceJobInfo | null;
  graph: WorkspaceJobInfo | null;
}

export interface LiveQuality {
  days: number;
  since: string;
  agent: { decisions: number; committed: number; sent_to_review: number; straight_through_rate: number | null; acknowledged_signals: number };
  reviews: { decided: number; approved: number; corrected: number; rejected: number; correction_rate: number | null };
  fields: { field: string; corrections: number; share_of_reviews: number | null; examples: { from: string | null; to: string | null }[] }[];
  calibration: { confidence_from: number; confidence_to: number; reviewed: number; changed_by_people: number; change_rate: number | null }[];
  last_evaluation: { id: string; completed_at: string; metrics: Partial<EvalMetrics> } | null;
}

export interface GoldenDocument {
  id: string;
  name: string;
  document_id: string | null;
  filename: string | null;
  complexity_tag: string;
  created_at: string;
  created_by: string | null;
  verified_in_review: boolean;
  rows: number;
  can_run: boolean;
}

export interface EvalMetrics {
  items: number;
  answered: number;
  failed: number;
  no_answer: number;
  field_accuracy: number | null;
  precision: number | null;
  f1_score: number | null;
  average_cer: number | null;
  exact_match_rate: number | null;
  straight_through_rate: number | null;
  confidently_wrong: number;
  confidently_wrong_rate: number | null;
  rules_pass_rate: number | null;
  average_confidence: number | null;
  most_missed_fields: { field: string; documents: number }[];
}

export interface EvalItem {
  id: string;
  name: string;
  document_id: string;
  status: "QUEUED" | "ANSWERED" | "FAILED" | "NO_ANSWER";
  decision: "COMMIT" | "REVIEW" | null;
  confidence: number | null;
  note: string | null;
  scores: { exact_match?: boolean; recall?: number; would_commit?: boolean; wrong_fields?: string[]; missing_fields?: string[] };
  expected: Record<string, unknown>;
  answer: Record<string, unknown> | null;
  completed_at: string | null;
}

export interface EvalRunInfo {
  id: string;
  status: "RUNNING" | "COMPLETED" | "CANCELLED";
  version_id: string | null;
  item_count: number;
  metrics: Partial<EvalMetrics>;
  requested_by: string;
  created_at: string;
  completed_at: string | null;
  items?: EvalItem[];
}

/** A plan as a workspace's settings show it (the organization's billing has the full catalog). Null: unlimited. */
export interface Plan {
  name: string;
  price_per_month: number;
  documents_per_month: number | null;
  members: number | null;
  api_keys: number | null;
  webhooks: number | null;
}

export interface Usage {
  documents_this_month: number;
  documents_total: number;
  storage_bytes: number;
  members: number;
  api_keys: number;
  entities: number;
  agent_runs_this_month: number;
  agent_input_tokens_this_month: number;
  agent_output_tokens_this_month: number;
  /** Null when no run this month has a configured model price. */
  agent_cost_usd_this_month: number | null;
  agent_runs_without_price: number;
  period_start: string;
}

/** One time the agent woke up: why, for whom, and what it cost. */
export interface AgentRun {
  id: string;
  run_id: string | null;
  trigger: string;
  status: string;
  model: string;
  origin: "user" | "system";
  source: "chat" | "dashboard" | "api" | "schedule" | "agent" | "system";
  actor_type: string;
  actor_id: string | null;
  actor_name: string | null;
  conversation_id: string | null;
  document_id: string | null;
  document_name: string | null;
  started_at: string;
  duration_ms: number | null;
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  steps: number;
  error: string | null;
  summary: string;
}

export interface AgentRunStep {
  step_number?: number;
  thought?: string;
  duration_ms?: number;
  tool_calls?: { tool_name: string; arguments?: unknown; observation?: string; duration_ms?: number; status?: string }[];
}

export interface AgentRunDetail extends Omit<AgentRun, "steps"> {
  steps: AgentRunStep[];
  final_thought: string | null;
  completed_at: string | null;
}

/** The organization's plan as one workspace sees it: the organization's pages, this workspace's share and its own limits. */
export interface Billing {
  plan: string;
  plans: Record<string, Plan>;
  usage: Usage;
  pages: { plan: string; plan_key: string; period_start: string; period_end: string; pages_used: number; pages_limit: number | null; credits: number };
  workspace_pages: number;
  limits: Record<string, number | null>;
  payment_method: null;
  invoices: never[];
  payments_enabled: boolean;
}

export interface Member {
  id: string;
  user_id: string;
  name: string;
  email: string | null;
  role: Role;
  role_label: string;
  created_at: string;
  is_you: boolean;
}

export interface OrgMemberInfo {
  user_id: string;
  name: string;
  email: string;
  role: OrgRole;
  role_label: string;
  created_at: string;
  is_you: boolean;
}

/** An invitation and how its email went (NOT_SENT: the link was only copied). */
export interface InvitationInfo {
  id: string;
  email: string;
  role: string;
  role_label: string;
  status: "PENDING" | "ACCEPTED" | "REVOKED" | "EXPIRED";
  invited_by: string;
  expires_at: string;
  created_at: string;
  email_status: "NOT_SENT" | "SENDING" | "SENT" | "FAILED";
  email_sent_at: string | null;
  email_error: string | null;
  note: string | null;
}

export interface OrgInvitationInfo extends InvitationInfo {
  role: OrgRole;
}

export interface Invitation extends InvitationInfo {
  role: Role;
}

/** What creating, resending or renewing an invitation returns: the invitation and its link (also emailed). */
export interface InviteIssued {
  invitation: InvitationInfo;
  token: string;
  link: string;
}

/** A role you can give people in this workspace; its description is the job title it stands for. */
export interface RoleInfo {
  key: Role;
  id: Role;
  label: string;
  name: string;
  description: string;
  system: boolean;
  members: number;
  permissions: Permission[];
}

export interface PermissionGroup {
  key: string;
  label: string;
  permissions: { key: Permission; label: string; description: string }[];
}

export interface RoleCatalogue {
  roles: RoleInfo[];
  permissions: { key: Permission; label: string }[];
  groups: PermissionGroup[];
  /** Ticking the left one always includes the right ones. */
  implies: Record<string, Permission[]>;
  can_manage: boolean;
}

export interface ApiKeyInfo {
  id: string;
  name: string;
  prefix: string;
  scope: "read" | "write";
  created_by: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface WorkspaceEventType {
  type: string;
  label: string;
  notify_by_default: boolean;
}

export interface WebhookInfo {
  id: string;
  url: string;
  description: string;
  events: string[];
  active: boolean;
  created_by: string;
  created_at: string;
  last_delivery: { ok: boolean; status_code: number | null; error: string | null; at: string } | null;
}

export interface WebhookDeliveryInfo {
  id: string;
  event_type: string;
  ok: boolean;
  status_code: number | null;
  error: string | null;
  duration_ms: number | null;
  at: string;
}

export interface NotificationPref extends WorkspaceEventType {
  enabled: boolean;
  /** False when your role doesn't cover this kind of event. */
  available: boolean;
}

export interface AuditEntry {
  id: string;
  event_type: string;
  entity_type: string;
  entity_id: string;
  actor: string;
  details: Record<string, unknown>;
  created_at: string;
}
