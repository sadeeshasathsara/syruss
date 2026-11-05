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
