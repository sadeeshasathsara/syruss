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
