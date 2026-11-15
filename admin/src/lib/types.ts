export type StaffRole = "owner" | "operator" | "support" | "billing" | "viewer";

/** You, as the platform staff sees you. What the app shows follows from `permissions`; the API checks them again. */
export interface StaffMe {
  user_id: string;
  name: string;
  email: string | null;
  role: StaffRole;
  role_name: string;
  permissions: string[];
  /** An owner named by the deployment (PLATFORM_ADMIN_USER_IDS). */
  from_config: boolean;
  roles: Record<StaffRole, string>;
  permission_names: Record<string, string>;
  /** The admin app signs out after idle_minutes without use; the sign-in itself ends at ends_at. */
  session: { idle_minutes: number; ends_at: string };
}

export type ApprovalStatus = "pending" | "expired" | "done" | "rejected" | "cancelled" | "failed";

export interface Approval {
  id: string;
  action: string;
  category: string | null;
  summary: string;
  reason: string | null;
  target_type: string;
  target_id: string;
  tenant_id: string | null;
  requested_by: string;
  requested_by_name: string;
  status: ApprovalStatus;
  created_at: string;
  expires_at: string;
  decided_by_name: string | null;
  decided_at: string | null;
  note: string | null;
}

export interface ApprovalList {
  items: Approval[];
  /** Waiting and not expired. */
  waiting: number;
}

export interface ApprovalRule {
  on: boolean;
  categories: string[];
  all_categories: Record<string, string>;
  /** Owners who can approve (the rule needs two). */
  approvers: number;
  hours: number;
}

export interface StaffPolicy {
  inactive_days: number;
  warn_days: number;
  review_days: number;
}

export interface PlatformStats {
  organizations: { total: number; active: number; suspended: number; closed: number; new_this_week: number };
  people: { total: number; active: number; disabled: number; new_this_week: number; seen_this_week: number };
  workspaces: { total: number };
  documents: { total: number; today: number; failed: number; stuck: number };
  agent: { runs_today: number; failed_today: number; events_waiting: number };
  deliveries: { dead: number };
}

export type ServiceStatus = "ok" | "degraded" | "down";

export interface PlatformHealth {
  checked_at: string;
  services: { key: string; name: string; status: ServiceStatus; latency_ms: number; detail: string }[];
}

export interface StaffMember {
  user_id: string;
  name: string;
  email: string | null;
  role: StaffRole;
  permissions: string[];
  from_config: boolean;
  added_by: string | null;
  added_at: string | null;
  last_seen_at: string | null;
  status: string;
  /** Their use of the admin app and what follows from it; null for the deployment's own owners. */
  last_active_at?: string | null;
  reviewed_at?: string | null;
  reviewed_by_name?: string | null;
  review_due_at?: string | null;
  review_due?: boolean;
  removal_at?: string | null;
}

export interface StaffList {
  items: StaffMember[];
  roles: Record<StaffRole, string>;
  policy: StaffPolicy;
}

export interface StaffAction {
  id: string;
  created_at: string;
  actor_id: string;
  actor_name: string;
  action: string;
  target_type: string;
  target_id: string;
  tenant_id: string | null;
  reason: string | null;
  details: Record<string, unknown>;
  ip: string | null;
}

export interface StaffActionPage {
  items: StaffAction[];
  next_before: string | null;
}

/** Organizations and workspaces named in a response (their names are facts, shown to all staff). */
export interface Names {
  organizations: Record<string, { name: string; slug: string; status: string }>;
  workspaces: Record<string, { name: string; slug: string; tenant_id: string }>;
}

export type RunStatus = "RUNNING" | "COMPLETED" | "FAILED" | "BUDGET_EXCEEDED" | "CANCELLED" | "DEAD_LETTERED" | "LOST";

/** An agent run as facts: never what it thought, read or wrote. */
export interface RunFacts {
  id: string;
  run_id: string | null;
  event_id: string | null;
  tenant_id: string;
  template_id: string;
  trigger: string;
  status: RunStatus;
  model: string;
  attempt: number;
  origin: string;
  source: string;
  actor_type: string;
  document_id: string | null;
  conversation_id: string | null;
  queued_at: string | null;
  started_at: string;
  completed_at: string | null;
  last_step_at: string | null;
  duration_ms: number | null;
  step: number | null;
  tool: string | null;
  steps: number;
  tool_calls: number;
  tool_errors: number;
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  failed: boolean;
  content_open?: boolean;
  document_name?: string | null;
}

export interface RunList extends Names {
  items: RunFacts[];
  next_before?: string | null;
  totals?: { runs: number; failed: number; running: number; cost_usd: number; tokens: number };
  triggers?: string[];
  models?: string[];
}

export interface EventFacts {
  id: string;
  type: string;
  tenant_id: string;
  template_id: string;
  created_at: string;
  published_at: string | null;
  attempts: number;
  last_error: string | null;
  cancelled_at: string | null;
  cancelled_by: string | null;
  actor: { type?: string; origin?: string; source?: string };
  document_id: string | null;
  conversation_id: string | null;
}

/** A step: in full with content open, else its shape only (thought_chars, tool names, timings). */
export interface StepUsage {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  [detail: string]: number | undefined;
}

export interface RunStep {
  step_number: number;
  step_started_at: string | null;
  step_completed_at: string | null;
  /** What the model answered in this step */
  thought?: string;
  /** The model's own thinking, when the provider returns it */
  reasoning?: string;
  /** What the loop added before this model call (a reminder to finish, requested page images) */
  added?: string[];
  usage?: StepUsage;
  tool_calls: { tool_call_id?: string; tool_name: string; status: string; duration_ms: number | null; arguments?: unknown; observation?: string }[];
}

/** What a run was given before its first model call. */
export interface RunPrompt {
  system: string | null;
  system_hash: string | null;
  briefing: string | null;
  tools: string[];
}

/** A run of the same document before or after this one. */
export interface RunLink {
  id: string;
  trigger: string;
  status: RunStatus;
  started_at: string;
  attempt: number;
}

export interface RunChange {
  at: string;
  kind: "log" | "change" | "message" | "conversation";
  what: string;
  level?: string;
  text?: string;
  target?: string;
  details?: Record<string, unknown>;
  document_id?: string | null;
  conversation_id?: string;
}

export interface RunDetail extends Names {
  run: RunFacts;
  event: EventFacts | null;
  tries: RunFacts[];
  changes: RunChange[];
  steps: RunStep[];
  final_thought: string | null;
  error: string | null;
  usage: Record<string, number>;
  prompt: RunPrompt | null;
  previous: RunLink | null;
  next: RunLink | null;
  /** Its place among the document's runs (1 = the first) */
  position: { number: number | null; of: number } | null;
  document: { id: string; name: string; content_type: string; size_bytes: number; page_count: number; status: string } | null;
}

export interface RunStats extends Names {
  since: string;
  unit: "hour" | "day";
  series: { at: string; runs: number; failed: number; cost_usd: number; p50_ms: number | null; p95_ms: number | null }[];
  by_trigger: { key: string; runs: number; failed: number; cost_usd: number }[];
  by_model: { key: string; runs: number; failed: number; cost_usd: number }[];
  by_status: { key: string; runs: number; failed: number; cost_usd: number }[];
  by_organization: { key: string; runs: number; failed: number; cost_usd: number }[];
}

export interface DocFacts {
  id: string;
  tenant_id: string;
  template_id: string;
  status: string;
  role: string;
  is_test: boolean;
  via: string;
  content_type: string;
  size_bytes: number;
  page_count?: number;
  created_at: string;
  updated_at: string;
  name?: string;
  filename?: string;
  external_id?: string | null;
  error?: string | null;
  has_error?: boolean;
}

export interface ProcessList extends Names {
  items: (DocFacts & { stuck: boolean; runs: number; last_run: RunFacts | null; content_open: boolean })[];
  next_before: string | null;
  totals: Record<string, number>;
}

export interface TimelineItem {
  at: string;
  stage: string;
  title: string;
  level: "info" | "success" | "warning" | "error";
  detail?: string;
  run?: string;
  event_id?: string;
  by?: string;
  duration_ms?: number;
  priority?: string;
  /** Everything known about the step, to show when it is opened */
  details: StepFact[];
}

/** One line of a journey step's details: text (or a number), JSON to format, or a block of code-like text. */
export interface StepFact {
  label: string;
  value: unknown;
  kind: "text" | "json" | "code";
}

export interface ProcessDetail extends Names {
  document: DocFacts;
  timeline: TimelineItem[];
  timings: {
    total_ms: number | null; queue_wait_ms: number | null; agent_ms: number | null; waiting_for_people_ms: number | null; delivery_ms: number | null;
    cost_usd: number | null; input_tokens: number; output_tokens: number; tokens: number; model_calls: number;
  };
  events: EventFacts[];
  runs: RunFacts[];
  changes: RunChange[];
  record: { data: Record<string, unknown>; confidence: number; validation: Record<string, unknown>; notes: string | null; revision: number; updated_at: string } | null;
}

export interface QueuesPage extends Names {
  checked_at: string;
  outbox: { kafka_connected: boolean; unsent: number; oldest_unsent_at: string | null; held_by_budget: number; held_workspaces: number; retrying: number; items: EventFacts[]; retrying_items: EventFacts[] };
  not_picked_up: { count: number; items: EventFacts[] };
  consumers: { status: "ok" | "down" | "unknown"; detail?: string; group?: string; topic?: string; lag?: number; partitions?: { partition: number; end: number; committed: number; lag: number }[] };
  runs: { running: number; lost_today: number; failed_today: number; running_items: RunFacts[]; failed_items: RunFacts[] };
  documents: { stuck: number; items: DocFacts[] };
  deliveries: { dead: number; retrying: number; held: number };
}

/** A fact on the platform's live view (sysadmin/live). */
export interface LiveFact {
  seq?: number;
  kind: string;
  at?: string;
  tenant_id?: string;
  template_id?: string;
  run_id?: string;
  trace_id?: string;
  document_id?: string;
  conversation_id?: string;
  status?: string;
  stage?: string;
  level?: string;
  step?: number;
  tool?: string;
  ok?: boolean;
  trigger?: string;
  cost_usd?: number | null;
  duration_ms?: number | null;
  count?: number;
  what?: string;
  content?: boolean;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

// ---- Billing (/sysadmin/billing) ----

export type CatalogKind = "plan" | "addon" | "credits";
export type Limits = Record<string, number | null>;

export interface LimitInfo {
  key: string;
  label: string;
  noun: string;
  /** The noun for exactly one: "API key". */
  one: string;
  scope: "workspace" | "organization";
  metered: boolean;
}

/** A way invoices can be paid here. */
export interface PaymentMethod {
  key: string;
  label: string;
  customer_can_use: boolean;
  needs_proof: boolean;
  online: boolean;
}

export interface BillingSetup {
  limits: LimitInfo[];
  methods: PaymentMethod[];
}

/** One version of a plan, an add-on or a credit pack. A published one never changes. */
export interface CatalogItem {
  id: string;
  kind: CatalogKind;
  key: string;
  version: number;
  name: string;
  description: string | null;
  status: "draft" | "published" | "archived";
  price_cents: number;
  currency: string;
  interval: "month" | "once";
  public: boolean;
  sort: number;
  limits: Limits;
  pages: number | null;
  created_by: string;
  created_at: string;
  published_at: string | null;
  archived_at: string | null;
  organizations: number;
}

export interface CatalogDraft {
  kind?: CatalogKind;
  key?: string;
  name?: string;
  description?: string | null;
  price_cents?: number;
  currency?: string;
  public?: boolean;
  sort?: number;
  limits?: Limits;
  pages?: number | null;
  reason: string;
}

export interface OrgBillingRow {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  plan_key: string;
  subscription_status: SubscriptionStatus;
  trial_ends_at: string | null;
  paid_until: string | null;
  pages_used: number;
  pages_limit: number | null;
  credits: number;
  period_end: string;
  invoices_waiting: number;
  invoices_overdue: number;
  drafts: number;
}

export type SubscriptionStatus = "trialing" | "active" | "past_due" | "canceled";
export type InvoiceStatus = "draft" | "open" | "paid" | "void" | "uncollectible";
type OrgRef = { name: string; slug: string; status: string };

export interface PaymentFileInfo {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  created_at: string;
}

export interface BillingPayment {
  id: string;
  invoice_id: string | null;
  invoice?: string | null;
  method: string;
  amount_cents: number;
  currency: string;
  item_id?: string | null;
  quantity?: number;
  received_on: string | null;
  reference: string | null;
  note?: string | null;
  recorded_by_name: string;
  created_at: string;
  files?: PaymentFileInfo[];
}

export interface InvoiceLine {
  id: string;
  kind: "plan" | "addon" | "credits" | "proration" | "custom";
  item_id: string | null;
  description: string;
  quantity: number;
  unit_cents: number;
  amount_cents: number;
  tax_rate_bp: number;
  period_start: string | null;
  period_end: string | null;
}

export interface Party {
  legal_name?: string;
  email?: string;
  address?: string;
  address_line1?: string;
  address_line2?: string | null;
  city?: string;
  region?: string | null;
  postal_code?: string | null;
  country?: string;
  tax_id?: string | null;
  po_number?: string | null;
}

export interface Submission {
  id: string;
  invoice_id: string;
  tenant_id: string;
  method: string;
  amount_cents: number;
  currency: string;
  paid_on: string;
  reference: string | null;
  note: string | null;
  status: "submitted" | "approved" | "rejected" | "withdrawn";
  submitted_by_name: string;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  rejection_reason: string | null;
  payment_id: string | null;
  created_at: string;
  files: PaymentFileInfo[];
  /** In the staff queue: the invoice, the organization and what to look at twice. */
  invoice?: Invoice;
  organization?: OrgRef;
  warnings?: string[];
}

export interface CreditNoteInfo {
  id: string;
  number: string;
  invoice_id: string;
  amount_cents: number;
  currency: string;
  reason: string;
  created_by_name: string;
  created_at: string;
  has_pdf: boolean;
  refunds: { amount_cents: number; method: string; reference: string | null; created_at: string }[];
}

export interface Invoice {
  id: string;
  number: string | null;
  tenant_id: string;
  kind: "purchase" | "renewal" | "upgrade" | "manual";
  status: InvoiceStatus;
  currency: string;
  subtotal_cents: number;
  tax_cents: number;
  total_cents: number;
  amount_paid_cents: number;
  amount_credited_cents: number;
  amount_refunded_cents: number;
  amount_due_cents: number;
  issued_at: string | null;
  due_at: string | null;
  paid_at: string | null;
  voided_at: string | null;
  void_reason: string | null;
  period_start: string | null;
  period_end: string | null;
  overdue: boolean;
  seller: Party;
  buyer: Party;
  tax_lines: { name: string; rate_bp: number; taxable_cents: number; tax_cents: number; note: string | null }[];
  note: string | null;
  switched_on: boolean;
  switched_on_early_by: string | null;
  switched_on_early_reason: string | null;
  has_pdf: boolean;
  has_receipt: boolean;
  created_at: string;
  organization?: OrgRef;
  lines?: InvoiceLine[];
  payments?: BillingPayment[];
  submissions?: Submission[];
  credit_notes?: CreditNoteInfo[];
  billing_details_missing?: string[];
}

export interface SellerSettings {
  legal_name: string;
  address: string;
  country: string;
  tax_id: string | null;
  email: string;
  footer: string | null;
  invoice_prefix: string;
  payment_terms_days: number;
  renewal_lead_days: number;
  grace_days: number;
  reminder_days: number[];
  updated_at: string;
  missing: string[];
}

export interface BankAccountInfo {
  id: string;
  bank: string;
  account_name: string;
  account_number: string;
  branch: string | null;
  swift: string | null;
  currency: string;
  instructions: string | null;
  active: boolean;
}

export interface TaxRateInfo {
  id: string;
  country: string;
  name: string;
  rate_bp: number;
  reverse_charge_with_tax_id: boolean;
  active: boolean;
  created_at: string;
}

export interface BillingSettingsPage {
  seller: SellerSettings;
  bank_accounts: BankAccountInfo[];
  tax_rates: TaxRateInfo[];
  methods: PaymentMethod[];
}

export interface LimitOverride {
  id: string;
  key: string;
  value: number | null;
  template_id: string | null;
  workspace: string | null;
  reason: string;
  set_by: string;
  created_at: string;
  expires_at: string | null;
}

export interface OrgBilling {
  organization: { id: string; name: string; slug: string; status: string };
  workspaces: { id: string; name: string }[];
  subscription: {
    plan: CatalogItem;
    pending_plan: CatalogItem | null;
    status: SubscriptionStatus;
    period_start: string;
    period_end: string;
    anchor_day: number;
    trial_ends_at: string | null;
    paid_until: string | null;
  };
  limits: {
    plan: Limits;
    effective: Limits;
    addons: { id: string; addon: CatalogItem; quantity: number; added_at: string }[];
    overrides: LimitOverride[];
  };
  usage: { pages_used: number; pages_limit: number | null; by_workspace: { id: string; name: string | null; pages: number }[] };
  credits: { balance: number; history: { id: string; delta: number; kind: string; reason: string | null; ref: string | null; created_by: string; created_at: string }[] };
  payments: BillingPayment[];
  invoices: Invoice[];
  billing_details: Party | null;
  billing_details_missing: string[];
}

/** An organization as staff find it: whether it opens, and how big it is. Never its content. */
export interface OrganizationAccount {
  id: string;
  name: string;
  slug: string;
  status: "active" | "suspended" | "closed";
  status_reason: string | null;
  status_changed_at: string | null;
  purged_at: string | null;
  created_at: string;
  members: number;
  workspaces: number;
}

export interface PersonAccount {
  id: string;
  name: string;
  email: string;
  status: "active" | "disabled" | "deleted";
  last_seen_at: string | null;
  created_at: string;
  organizations: { id: string; name: string; role: string }[];
  is_staff: boolean;
}

/** Where the money stands in one currency (owed, overdue, received, recurring). */
export interface MoneySummary {
  currency: string;
  owed_cents: number;
  owed_invoices: number;
  overdue_cents: number;
  overdue_invoices: number;
  invoiced_this_month_cents: number;
  received_this_month_cents: number;
  received_last_month_cents: number;
  monthly_recurring_cents: number;
  paying_organizations: number;
}

export interface FollowUp {
  tenant_id: string;
  organization?: { name: string; slug: string };
  plan: string;
  status: string;
  trial_ends_at: string | null;
  paid_until: string | null;
  period_end: string;
}

// ---- the operator's console ---------------------------------------------------------------------

export interface DayPoint { day: string; value: number }

export interface BusinessMetrics {
  as_of: string;
  revenue: { currency: string; mrr_cents: number; arr_cents: number }[];
  organizations: { active: number; paying: number; trialing: number; active_7d: number; active_30d: number; closed_this_month: number; lapsed_this_month: number };
  runs: { last_30d: number; failed_30d: number };
  series: { organizations: DayPoint[]; people: DayPoint[]; documents: DayPoint[]; pages: DayPoint[]; agent_cost_usd: DayPoint[] };
}

export interface RiskReason { code: string; text: string; level: "high" | "medium" | "low" }
export interface AtRisk { tenant_id: string; name: string; slug: string | null; plan: string | null; reasons: RiskReason[] }

export interface OrgNote { id: string; body: string; pinned: boolean; author_name: string; author_id: string; created_at: string }

export interface OrganizationProfile {
  organization: { id: string; name: string; slug: string; status: "active" | "suspended" | "closed"; status_reason: string | null; status_changed_at: string | null; created_at: string; purged_at: string | null };
  plan: { name: string; key: string; price_cents: number; currency: string; status: string; period_start: string; period_end: string; trial_ends_at: string | null; paid_until: string | null };
  usage: { pages_used: number; pages_limit: number | null; credits: number; documents_30d: DayPoint[]; runs_30d: number; agent_cost_30d_usd: number; last_run_at: string | null };
  money: { open_invoices: number; owed_cents: number; currency: string; overdue: number };
  members: { user_id: string; name: string; email: string; role: string; status: string; last_seen_at: string | null; joined_at: string }[];
  workspaces: { id: string; name: string; slug: string; documents: number; last_document_at: string | null; members: number; created_at: string }[];
  notes: OrgNote[];
  staff_actions: StaffAction[];
  agent_paused: boolean;
}

export interface PersonProfile {
  person: { id: string; name: string; email: string; email_verified: boolean; status: "active" | "disabled" | "deleted"; created_at: string; last_seen_at: string | null; timezone: string; staff_role: string | null };
  organizations: { id: string; name: string; status: string; role: string }[];
  workspaces: { id: string; name: string; tenant_id: string; role: string }[];
  browsers: { id: string; label: string; ip: string; last_seen_at: string; trusted_until: string | null }[];
  security_events: { kind: string; summary: string; device: string; ip: string; created_at: string }[];
  staff_actions: StaffAction[];
}

export type Severity = "info" | "warning" | "critical";
export interface Announcement {
  id: string; title: string; body: string; severity: Severity; tenant_id: string | null; organization: string | null;
  starts_at: string; ends_at: string | null; created_by_name: string; created_at: string; state: "live" | "scheduled" | "ended";
}
export type SwitchName = "signups" | "agent" | "intake";
export interface PlatformControls {
  maintenance: { on: boolean; message: string };
  switches: Record<SwitchName, boolean>;
  agent_paused_orgs: { id: string; name: string }[];
  announcements: Announcement[];
}

export interface SecurityView {
  last_7_days: { wrong_codes: number; browsers_confirmed: number; browsers_removed: number; codes_sent: number };
  wrong_code_bursts: { user_id: string; name: string; email: string | null; count: number; last_at: string }[];
  staff_review: { user_id: string; name: string; email: string; role: string; status: string; last_seen_at: string | null; last_action_at: string | null; stale: boolean }[];
}

// ---- joining the staff ---------------------------------------------------------------------------

export interface StaffInvitation {
  id: string; email: string; role: StaffRole; status: "pending" | "expired" | "accepted" | "revoked";
  expires_at: string; invited_by_name: string; reason: string | null; email_sent_at: string | null; created_at: string; accepted_at: string | null;
}

export interface JoinView {
  invitation: StaffInvitation;
  role_description: string;
  signed_in_as: string | null;
  problem: { code: string; message: string } | null;
  already_staff: boolean;
  customer_account: { organizations: { id: string; name: string; role: string; status: string; outcome: "closed" | "left" }[]; workspaces: number } | null;
}

export interface SystemLesson {
  id: string; content: string; status: "active" | "proposed" | "retired"; importance: number; source: string;
  why?: string | null; times_proposed?: number; created_at: string;
}

// ---- support -------------------------------------------------------------------------------------

export type TicketStatus = "new" | "open" | "pending" | "solved" | "closed";
export type TicketPriority = "low" | "normal" | "high" | "urgent";
export type TicketCategory = "billing" | "account" | "technical" | "other";

export interface Ticket {
  id: string;
  number: string;
  subject: string;
  category: TicketCategory;
  priority: TicketPriority;
  status: TicketStatus;
  channel: "agent" | "app" | "email";
  requester_name: string;
  requester_email: string;
  tenant_id: string | null;
  organization?: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
  unverified: boolean;
  related: { conversation_id?: string; document_id?: string; invoice_id?: string; follow_up_of?: string };
  first_response_due: string | null;
  resolve_due: string | null;
  first_responded_at: string | null;
  breached_at: string | null;
  solved_at: string | null;
  created_at: string;
  updated_at: string;
  last_customer_at: string | null;
  last_staff_at: string | null;
}

export interface TicketFile { id: string; filename: string; content_type: string; size: number }

export interface TicketMessage {
  id: string;
  author_kind: "customer" | "staff" | "agent" | "system";
  author_name: string;
  body: string;
  internal: boolean;
  via: "app" | "email" | "agent";
  created_at: string;
  files: TicketFile[];
}

/** Who a customer is, from what staff already see elsewhere (a ticket's or a live chat's side panel). */
export interface CustomerContext {
  organization: { id: string; name: string; slug: string; status: string; plan: string | null } | null;
  person: { id: string; name: string; email: string; status: string; last_seen_at: string | null } | null;
  bills: { id: string; number: string; status: string; total_cents: number; currency: string; due_cents: number; due_at: string | null }[];
}

export interface TicketDetail extends Ticket {
  messages: TicketMessage[];
  context: CustomerContext & { other_tickets: { id: string; number: string; subject: string; status: TicketStatus; created_at: string }[] };
}

export interface SupportSummary { new: number; unassigned: number; mine: number; breached: number; active: number; live_waiting: number }

export type LiveState = "waiting" | "live" | "ended";

/** A chat in live support, as the queue shows it (the assistant's handover, not the conversation). */
export interface LiveChatItem {
  conversation_id: string;
  state: LiveState;
  tenant_id: string;
  organization: string | null;
  workspace: string | null;
  person: string | null;
  reason: string | null;
  summary: string | null;
  requested_at: string | null;
  joined_at: string | null;
  staff_id: string | null;
  staff_name: string | null;
}

export interface LiveQueue { waiting: LiveChatItem[]; mine: LiveChatItem[]; others_live: number; online: { id: string; name: string }[] }

export interface LiveMessage { id: string; role: "user" | "assistant" | "system" | "staff"; content: string; staff_name: string | null; at: string }

export interface LiveChat extends LiveChatItem {
  title: string;
  support: { state: LiveState; ticket_number: string | null; rating: "up" | "down" | null } | null;
  rating_note: string | null;
  messages: LiveMessage[];
  context: CustomerContext;
}

export type Presence = "online" | "away" | "offline";

export type MailPurpose = "notifications" | "accounts" | "support";

/** A mailbox Syruss sends (and reads) mail through: from the mail service's environment, or connected here. No secrets. */
export interface MailAccount {
  id: string;
  kind: "smtp_imap" | "gmail" | "console" | "memory";
  address: string;
  name: string;
  granted: ("send" | "read")[];
  status: "active" | "needs_reconnect";
  last_error: string | null;
  checked_at?: string | null;
  from_environment: boolean;
  purposes: MailPurpose[];
  config?: Record<string, string | number | boolean>;
}

export interface MailOverview {
  google: boolean;
  redirect_uri: string;
  accounts: MailAccount[];
  purposes: Record<MailPurpose, { account_id: string; address: string; routed: boolean; needs: ("send" | "read")[]; reads: boolean }>;
}

export interface MailJob {
  id: string;
  purpose: MailPurpose;
  to: string;
  subject: string;
  status: "queued" | "sending" | "retrying" | "sent" | "dead";
  attempts: number;
  last_error: string | null;
  account_id: string | null;
  created_at: string;
  done_at: string | null;
  next_attempt_at: string | null;
  retryable: boolean;
}

export interface MailImapInput {
  address: string;
  name: string;
  smtp_host?: string;
  smtp_port: number;
  smtp_security: "none" | "starttls" | "ssl";
  smtp_username: string;
  smtp_password: string;
  imap_host?: string;
  imap_port: number;
  imap_ssl: boolean;
  imap_folder: string;
  imap_username: string;
  imap_password: string;
}

export interface SavedReply { id: string; title: string; body: string; shortcut: string | null; created_by_name: string; updated_at: string }

export interface SupportSettings {
  first_response_hours: Record<TicketPriority, number>;
  resolve_hours: Record<TicketPriority, number>;
  auto_solve_days: number;
  auto_close_days: number;
  /** reading null: the mail service couldn't be asked */
  mailbox: { address: string; reading: boolean | null; connected: boolean | null };
}
