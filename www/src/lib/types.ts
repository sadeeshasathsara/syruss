// Shapes returned by the core API (server/app/schemas).

export type DocumentStatus =
  | "PENDING"
  | "QUEUED"
  | "PROCESSING"
  | "AWAITING_INPUT"
  | "NEEDS_REVIEW"
  | "COMPLETED"
  | "FAILED";

export interface Organization {
  id: string;
  name: string;
  slug: string;
}

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  document_type: string;
  status: "draft" | "active";
  documents: number;
  needs_review: number;
  open_questions: number;
  pending_proposals: number;
  /** Conversations Syruss opened that are still open */
  open_conversations: number;
  /** Your role id in this workspace; null for an organization admin who isn't a member (can manage it, can't open it) */
  my_role?: string | null;
  my_role_name?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TemplateField {
  name: string;
  type?: string;
  required?: boolean;
  description?: string;
  resolves_to?: string;
}

export interface TemplateObject {
  type: string;
  description?: string;
  multiple?: boolean;
  identity?: { id_field: string; label_field: string };
  fields: Array<string | TemplateField>;
}

export interface ValidationRule {
  id?: string;
  rule_type: 'expression' | 'sum_match' | 'count_equals' | 'allowed_values' | 'regex' | 'unique' | 'time_diff' | string;
  object: string;
  description?: string;
  expression?: string;
  field?: string;
  total_object?: string;
  total_field?: string;
  tolerance?: number;
  expected_count?: number;
  count_object?: string;
  count_field?: string;
  values?: string[];
  pattern?: string;
  start_field?: string;
  end_field?: string;
  hours_field?: string;
  expected_hours?: number;
  tolerance_hours?: number;
  [key: string]: unknown;
}

export interface TemplateSchema {
  document_type?: string;
  description?: string;
  objects: TemplateObject[];
  validation_rules?: ValidationRule[];
}

export interface Template {
  id: string;
  name: string;
  document_type: string;
  description: string | null;
  schema_definition: TemplateSchema;
  active_version_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface TemplateVersion {
  id: string;
  version: string;
  status: "DRAFT" | "STAGED" | "ACTIVE" | "ARCHIVED";
  schema_definition: TemplateSchema;
  change_summary: string | null;
  created_by: string;
  approved_by: string | null;
  created_at: string;
  activated_at: string | null;
}

export interface DocumentItem {
  id: string;
  template_id: string;
  role: "DOCUMENT" | "SAMPLE";
  original_filename: string;
  content_type: string;
  file_size_bytes: number;
  status: DocumentStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface FieldEvidence {
  object?: string;
  index?: number;
  field?: string;
  value?: unknown;
  raw_text?: string;
  confidence?: number;
  entity_id?: string | null;
  note?: string;
}

export interface Extraction {
  id: string;
  document_id: string;
  template_version_id: string | null;
  extracted_data: Record<string, Record<string, unknown> | Record<string, unknown>[]>;
  overall_confidence: number;
  evidence_records: FieldEvidence[];
  validation: {
    valid?: boolean;
    errors?: { code: string; message: string; path?: string }[];
    /** The workspace graph's identity checks: likely misreads and first-time appearances. */
    signals?: { id: string; code: string; path: string; message: string; blocking: boolean; acknowledged?: boolean; reason?: string }[];
  };
  agent_notes: string | null;
  updated_at: string;
}

export interface ReviewTask {
  id: string;
  document_id: string;
  reason: string;
  uncertain_fields: { field: string; value?: unknown; why?: string }[];
  status: "PENDING" | "APPROVED" | "CORRECTED" | "REJECTED";
  original_data: Extraction["extracted_data"];
  corrected_data: Extraction["extracted_data"] | null;
  reviewer_notes: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface AgentQuestion {
  id: string;
  question: string;
  context: string;
  options: string[];
  /** text: a written answer; single: one option; multiple: any options. Choices always allow "Other". */
  answer_type: "text" | "single" | "multiple";
  evidence_document_ids: string[];
  /** What the agent read that the question is about, as a table. */
  data_table?: { title?: string | null; columns: string[]; rows: string[][] } | null;
  target_role: "admin" | "reviewer";
  priority: "low" | "normal" | "high";
  related_document_id: string | null;
  /** The flag's own chat thread; answered flags stay pending until Syruss resolves it. */
  conversation_id?: string | null;
  conversation_status?: "open" | "resolved" | null;
  status: "OPEN" | "ANSWERED" | "DISMISSED";
  answer: string | null;
  answer_choices?: string[] | null;
  answer_other?: string | null;
  answered_by: string | null;
  answered_at?: string | null;
  created_at: string;
}

export interface FlagAnswer {
  answer?: string;
  choices?: string[];
  other?: string;
}

export interface Proposal {
  id: string;
  proposal_type: "TEMPLATE_CHANGE" | "ENTITY_CREATE" | "ENTITY_MERGE" | "SOURCE_MAPPING";
  observation: string;
  proposed_data: {
    schema_definition?: TemplateSchema;
    change_summary?: string;
    name?: string | null;
    [key: string]: unknown;
  };
  evidence_document_ids: string[];
  evidence: Record<string, unknown>;
  status: "PENDING" | "APPROVED" | "REJECTED";
  reviewer_notes: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface Entity {
  id: string;
  entity_type: string;
  name: string;
  identifier_key: string | null;
  identifier_value: string | null;
  attributes: Record<string, unknown>;
  aliases: string[];
  created_by: string;
  source_document_id: string | null;
  created_at: string;
}

/** How a source's records become known things: decided by the agent, approved by a person. */
export interface SourceMapping {
  records_path: string;
  objects: {
    entity_type: string;
    id_column: string;
    name_column?: string;
    attributes?: Record<string, string>;
    alias_columns?: string[];
  }[];
}

export interface MappingPreview {
  entity_type: string;
  rows: number;
  records: number;
  new: number;
  already_known: number;
  names_differ: number;
  rows_without_id: number;
  ids_written_two_ways: number;
  examples: { id: string; name: string; attributes: Record<string, unknown>; aliases: string[] }[];
}

export interface RecordSet {
  records_path: string;
  count: number;
  columns: string[];
  sample?: Record<string, unknown>[];
}

export type ApiAuthType = "none" | "bearer" | "basic" | "api_key" | "oauth2_client_credentials";

/** What you type to connect an API. Secret fields left blank keep what is saved. */
export interface ApiSourceInput {
  name: string;
  url: string;
  method: "GET" | "POST";
  query: Record<string, string>;
  headers: Record<string, string>;
  body?: string | null;
  notes?: string;
  auth: {
    type: ApiAuthType;
    token?: string;
    username?: string;
    password?: string;
    key_name?: string;
    key_in?: "header" | "query";
    key_value?: string;
    token_url?: string;
    client_id?: string;
    client_secret?: string;
    scope?: string;
  };
}

export interface KnowledgeSource {
  id: string;
  name: string;
  kind: "FILE" | "API";
  notes: string;
  status: "MAPPING" | "REVIEW" | "READY" | "ERROR";
  last_error: string | null;
  imported_count: number;
  last_synced_at: string | null;
  created_at: string;
  record_sets: RecordSet[];
  mapping: SourceMapping | null;
  /** Saved API settings. Secrets are never sent back: only which ones are saved. */
  api?: {
    url: string;
    method: "GET" | "POST";
    query: Record<string, string>;
    header_names: string[];
    body: string | null;
    auth: Omit<ApiSourceInput["auth"], "token" | "password" | "key_value" | "client_secret"> & { saved_secrets: string[] };
  };
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[] | null;
  message_count?: number;
  /** chat: your own chat with Syruss. agent: a conversation Syruss opened about an issue. flag: a flag's thread. */
  kind?: "chat" | "agent" | "flag";
  status?: "open" | "resolved" | null;
  audience?: "admin" | "reviewer" | null;
  resolution?: string | null;
  context_metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface SystemLesson {
  id: string;
  content: string;
  status: "active" | "proposed" | "retired";
  source: "seed" | "agent" | "admin";
  importance: number;
  why: string | null;
  times_proposed: number;
  created_at: string;
}

/** A row of a conversation list: never its messages. */
export interface ConversationSummary {
  id: string;
  title: string;
  kind: "chat" | "agent" | "flag";
  status: "open" | "resolved" | null;
  audience: "admin" | "reviewer" | null;
  updated_at: string;
  created_at: string;
}

export interface ConversationPage {
  items: ConversationSummary[];
  has_more: boolean;
  next_offset: number;
  /** Agent conversations still open (kind=agent only). */
  open?: number;
}

/** A live event from the workspace channel (agent telemetry or a server notification). */
export interface LiveEvent {
  event_type: string;
  channel_id?: string;
  document_id?: string;
  template_id?: string;
  run_id?: string;
  timestamp?: string;
  source?: "agent" | "core";
  [key: string]: unknown;
}

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
  plan: "free" | "team" | "business";
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

export interface Plan {
  name: string;
  price_per_month: number;
  documents_per_month: number;
  members: number;
  api_keys: number;
  webhooks: number;
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

export interface Billing {
  plan: WorkspaceSettingsData["plan"];
  plans: Record<WorkspaceSettingsData["plan"], Plan>;
  usage: Usage;
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

// ---------------------------------------------------------------------------
// The signed-in person
// ---------------------------------------------------------------------------

/** An organization you belong to, with your role in it. `last_used` marks the one you were last in. */
export interface MyOrganization {
  id: string;
  slug: string;
  name: string;
  role: OrgRole;
  role_label: string;
  last_used: boolean;
}

export interface Me {
  user_id: string;
  tenant_id: string;
  name: string;
  email: string | null;
  job_title: string | null;
  avatar_url?: string | null;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  timezone: string;
  org_roles: string[];
  org_role: OrgRole | null;
  org_permissions: OrgPermission[];
  is_platform_admin: boolean;
}

export interface AppNotification {
  id: string;
  type: string;
  title: string;
  body: string;
  link: string | null;
  severity: "info" | "success" | "warning" | "error";
  workspace_id: string | null;
  data: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export type MemoryKind = "lesson" | "observation" | "fact" | "preference" | "summary";

/** Something Syruss remembers: about the workspace (shared) or about you (private). */
export interface MemoryItem {
  id: string;
  scope: "workspace" | "personal" | "conversation";
  kind: MemoryKind;
  content: string;
  data: Record<string, unknown>;
  importance: number;
  pinned: boolean;
  source: { type?: string; event_id?: string; conversation_id?: string; document_ids?: string[] };
  created_by: string;
  use_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvitationPreview {
  workspace: { id: string; name: string; slug: string };
  organization: { name: string; slug: string };
  email: string;
  role: Role;
  role_label: string;
  invited_by: string;
  status: Invitation["status"];
  expires_at: string;
}

/** One line of the extraction log: what happened to a document, in plain words. Read-only for everyone. */
export interface LogEntry {
  seq: number;
  id: string;
  stage: "received" | "queued" | "reading" | "waiting" | "saved" | "review" | "failed" | "decided" | "corrected";
  level: "info" | "success" | "warning" | "error";
  message: string;
  document_id: string | null;
  document_name: string | null;
  actor: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface LogPage {
  items: LogEntry[];
  /** Pass as `before` to get the next older page; null when there are no more. */
  next_before: number | null;
}

export interface Overview {
  days: { date: string; received: number; saved: number; review: number; failed: number; working: number }[];
  totals: { received: number; saved: number; needs_review: number; waiting: number; failed: number; working: number };
  /** Share of recently saved documents that never needed a person (0 to 1). */
  hands_free_rate: number | null;
  avg_seconds_to_save: number | null;
  last_activity_at: string | null;
  usage: { documents_this_month: number; documents_limit: number; members: number; members_limit: number; plan: string };
}

export interface OrgOverview {
  organization: { name: string; slug: string; created_at: string };
  workspaces: { total: number; draft: number; mine: number; reachable: number };
  people: { total: number; owners: number; admins: number; members: number };
  /** Only for people who manage members. */
  pending_invitations: number | null;
  attention: { needs_review: number; questions: number; drafts: number };
  /** Only for people who may read the organization's activity. */
  activity: OrgActivityItem[] | null;
}

export interface OrgActivityItem {
  id: string;
  event: string;
  created_at: string;
  actor: string;
  text: string;
}

export interface OrgActivityPage {
  items: OrgActivityItem[];
  next_before: string | null;
}

/** A browser you have signed in from and confirmed. The browser's secret never leaves it; this is only what to show. */
export interface TrustedDevice {
  id: string;
  label: string;
  ip: string;
  remember: boolean;
  trusted_at: string | null;
  trusted_until: string | null;
  last_seen_at: string;
  current: boolean;
}

export interface SecurityEvent {
  id: string;
  kind: string;
  summary: string;
  device: string;
  ip: string;
  created_at: string;
}

export interface SecurityEventPage {
  items: SecurityEvent[];
  next_before: string | null;
}


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

// ---------------------------------------------------------------------------
// Connections: receiving documents from other systems
// ---------------------------------------------------------------------------

export interface IntakeKeyInfo {
  id: string;
  name: string;
  prefix: string;
  mode: "live" | "test";
  state: "active" | "retiring" | "expired" | "revoked";
  created_by: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  ip_allowlist: string[];
  rate_limit_per_minute: number;
  daily_quota: number | null;
  replaced_by: string | null;
}

export interface IntakeChannelInfo {
  id: string;
  slug: string;
  name: string;
  kind: "api" | "email" | "sftp";
  status: "active" | "paused";
  created_by: string;
  created_at: string;
  config: { allowed_types: string[] | null; max_mb: number | null; process: boolean | null; sender_allowlist: string[]; require_authentication: boolean };
  endpoint: string | null;
  /** E-mail channels: the address people send documents to. */
  address: string | null;
  /** SFTP channels: where and as whom to connect. */
  sftp: { host: string; port: number; username: string; has_public_key: boolean } | null;
  /** Whether this server's receiver for the kind of channel is switched on. */
  receiver_ready: boolean;
  stats: { accepted_24h: number; rejected_24h: number; last_received_at: string | null };
  keys: IntakeKeyInfo[];
}

export interface IntakeEventInfo {
  id: number;
  outcome: "accepted" | "rejected";
  reason: string;
  filename: string;
  size_bytes: number;
  external_id: string | null;
  document_id: string | null;
  sender: string;
  ip: string;
  via: string;
  at: string;
}

export interface IntakeActivityPage {
  items: IntakeEventInfo[];
  next: number | null;
}

export interface DeveloperInfo {
  base_url: string;
  intake_url: string;
  accepts: string[];
  max_file_mb: number;
  upload_minutes: number;
  workspace: string;
  trusts_proxy_headers: boolean;
  email: { enabled: boolean; domain: string };
  sftp: { enabled: boolean; host: string; port: number };
}

export interface ChannelConfigPatch {
  allowed_types?: string[] | null;
  max_mb?: number | null;
  process?: boolean | null;
  sender_allowlist?: string[] | null;
  require_authentication?: boolean | null;
}

export interface NewIntakeKey {
  name: string;
  mode: "live" | "test";
  ip_allowlist: string[];
  rate_limit_per_minute: number;
  daily_quota: number | null;
  expires_in_days: number | null;
}

// ---------------------------------------------------------------------------
// Connections: publishing approved records to other systems
// ---------------------------------------------------------------------------

export interface DestinationField {
  key: string;
  label: string;
  kind: "text" | "url" | "password" | "number" | "select" | "textarea" | "checkbox";
  required: boolean;
  secret: boolean;
  help: string;
  placeholder: string;
  options: { value: string; label: string }[] | null;
  default: unknown;
  show_if: Record<string, string> | null;
}

export interface DestinationTarget {
  key: string;
  label: string;
  required: boolean;
  line: boolean;
  kind: "text" | "number" | "date";
}

export interface DestinationKind {
  kind: string;
  label: string;
  blurb: string;
  category: "custom" | "accounting" | "storage" | "mail";
  uses_oauth: boolean;
  /** For kinds that sign in with OAuth: the address to register in the vendor's app, where it sends the person back to. */
  redirect_uri?: string | null;
  fields: DestinationField[];
  targets: DestinationTarget[];
  corrections: boolean;
  maps: boolean;
  mapping_required: boolean;
}

export interface MappingField {
  target: string;
  source: "field" | "formula" | "constant";
  value: string;
}

export interface MappingSpec {
  mode?: "record" | "mapped";
  fields?: MappingField[];
  lines?: { target: string; from: string; fields: MappingField[] } | null;
}

export interface DestinationPolicy {
  send_updates: boolean;
  include_test: boolean;
  max_attempts: number;
}

export interface DestinationCounts {
  delivered: number;
  held: number;
  waiting: number;
  dead: number;
  delivered_24h: number;
}

export interface DestinationInfo {
  id: string;
  name: string;
  kind: string;
  label: string;
  category: DestinationKind["category"];
  status: "active" | "paused";
  paused_reason: string | null;
  paused_by: "user" | "system" | null;
  config: Record<string, unknown>;
  secrets_set: string[];
  mapping: MappingSpec;
  policy: DestinationPolicy;
  consecutive_failures: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  created_by: string;
  created_at: string;
  counts: DestinationCounts;
  health: { tone: "positive" | "negative" | "warning" | "neutral"; label: string };
  connected: boolean | null;
  signing_secret?: string;
}

export interface MappingSources {
  objects: { type: string; multiple: boolean; fields: { name: string; type: string }[] }[];
  documents: { id: string; filename: string; external_id: string | null }[];
  functions: { name: string; usage: string }[];
}

export type DeliveryStatus = "held" | "pending" | "sending" | "retrying" | "delivered" | "dead" | "cancelled";

export interface DeliveryRow {
  id: string;
  status: DeliveryStatus;
  hold_reason: string | null;
  revision: number;
  event: "document.completed" | "document.corrected";
  attempts: number;
  next_attempt_at: string | null;
  last_error: string | null;
  last_status_code: number | null;
  delivered_at: string | null;
  external_ref: string | null;
  created_at: string;
  updated_at: string;
  destination: { id: string; name: string; kind: string; status: string } | null;
  document: { id: string; filename: string; external_id: string | null; is_test: boolean } | null;
}

export interface DeliveryDetail extends DeliveryRow {
  idempotency_key: string;
  message: Record<string, unknown> | null;
  message_problems: string[];
  attempt_log: { no: number; ok: boolean; status_code: number | null; error: string | null; duration_ms: number | null; request: string | null; response: string | null; at: string }[];
}

export interface DeliveryPage {
  items: DeliveryRow[];
  total: number;
}

export interface DeliveryStats {
  by_status: Record<DeliveryStatus, number>;
  delivered_24h: number;
  waiting: number;
}

export interface ConnectionsOverview {
  state: "ok" | "attention" | "problem";
  receive: { channels: number; active: number; accepted_24h: number; rejected_24h: number };
  publish: { destinations: number; active: number; paused: number; delivered_24h: number; waiting: number; held: number; dead: number };
  held: { delivery_id: string; reason: string | null; destination: string; document: { id: string; filename: string; external_id: string | null }; since: string }[];
  held_reasons: { reason: string; count: number }[];
  problems: { kind: string; title: string; detail: string | null; destination_id?: string; delivery_id?: string; channel_id?: string; slug?: string; automatic?: boolean }[];
  failing: number;
}

export interface NewDestination {
  kind: string;
  name: string;
  config: Record<string, unknown>;
  secrets: Record<string, string>;
  mapping: MappingSpec;
  policy: Partial<DestinationPolicy>;
}

export interface TryResult {
  ok: boolean;
  message: string;
  detail: string | null;
}

export interface MappingPreview {
  envelope: Record<string, unknown>;
  problems: string[];
}
