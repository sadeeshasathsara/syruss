import type { Invitation, OrgPermission, OrgRole, Role } from "./settings";

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

export type OrgPeriod = 7 | 30 | 90;

/** How work went over a period, added up across workspaces. Rates are 0 to 1, null when there was nothing to rate. */
export interface OrgPeriodTotals {
  received: number;
  saved: number;
  review: number;
  failed: number;
  working: number;
  hands_free_rate: number | null;
  avg_seconds_to_save: number | null;
  failure_rate: number | null;
  runs: number;
  /** Null when you may not see costs, or when the agent ran on a model with no price set (then `tokens` still counts). */
  cost_usd: number | null;
  tokens: number | null;
}

export interface OrgWorkspaceHealth {
  id: string;
  slug: string;
  name: string;
  status: "draft" | "active";
  /** Null for an organization admin who manages the workspace without a role in it. */
  my_role: string | null;
  /** In the period. */
  received: number;
  saved: number;
  failed: number;
  hands_free_rate: number | null;
  /** Now. */
  needs_review: number;
  open_questions: number;
  open_threads: number;
  failed_now: number;
  last_activity_at: string | null;
  documents_this_month: number;
  documents_limit: number;
  plan: string;
  cost_usd: number | null;
  tokens: number | null;
  cost_this_month_usd: number | null;
  budget_usd: number | null;
}

export interface OrgOverview {
  organization: { name: string; slug: string; created_at: string };
  workspaces: { total: number; draft: number; mine: number; reachable: number };
  people: { total: number; owners: number; admins: number; members: number };
  /** Only for people who manage members. */
  pending_invitations: number | null;
  /** Across the workspaces you hold a role in. */
  attention: { needs_review: number; questions: number; threads: number; failed: number; drafts: number };
  /** The first steps of a new organization. */
  setup: { workspace: boolean; published: boolean; documents: boolean; people: boolean };
  /** Whether you may see what the agents cost (those who audit or manage billing). */
  costs: boolean;
  period: { days: OrgPeriod; from: string; to: string; series: Overview["days"]; current: OrgPeriodTotals; previous: OrgPeriodTotals };
  /** Your workspaces, and for an organization admin the others too. */
  workspace_health: OrgWorkspaceHealth[];
  /** Workspaces you hold a role in where something is waiting. */
  needs_you: { slug: string; name: string; status: "draft" | "active"; needs_review: number; questions: number; threads: number; failed: number }[];
  this_month: { documents: number; cost_usd: number | null; tokens: number | null; near_limit: string[] };
  /** The latest steps in the logs of the workspaces you hold a role in. */
  latest_work: { id: string; workspace: string; workspace_slug: string; document_id: string | null; stage: LogEntry["stage"]; level: LogEntry["level"]; message: string; created_at: string }[];
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
