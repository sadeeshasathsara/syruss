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
}

export interface StaffList {
  items: StaffMember[];
  roles: Record<StaffRole, string>;
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
export interface RunStep {
  step_number: number;
  step_started_at: string | null;
  step_completed_at: string | null;
  thought?: string;
  thought_chars?: number;
  tool_calls: { tool_name: string; status: string; duration_ms: number | null; arguments?: unknown; observation?: string }[];
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

export interface Grant {
  id: string;
  tenant_id: string;
  organization?: string | null;
  reason: string;
  created_at?: string;
  expires_at: string;
  ended_at?: string | null;
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
  grant: Grant | null;
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
}

export interface ProcessDetail extends Names {
  document: DocFacts;
  content_open: boolean;
  grant: Grant | null;
  timeline: TimelineItem[];
  timings: { total_ms: number | null; queue_wait_ms: number | null; agent_ms: number | null; waiting_for_people_ms: number | null; delivery_ms: number | null; cost_usd: number | null };
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

export interface GrantList {
  items: Grant[];
  may_open: boolean;
  minutes: { min: number; max: number; default: number };
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

export interface FollowUp {
  tenant_id: string;
  organization?: { name: string; slug: string };
  plan: string;
  status: string;
  trial_ends_at: string | null;
  paid_until: string | null;
  period_end: string;
}
