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
