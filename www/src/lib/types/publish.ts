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
  documents: { id: string; filename: string; display_name?: string | null; external_id: string | null }[];
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
  document: { id: string; filename: string; display_name?: string | null; external_id: string | null; is_test: boolean } | null;
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
  held: { delivery_id: string; reason: string | null; destination: string; document: { id: string; filename: string; display_name?: string | null; external_id: string | null }; since: string }[];
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
