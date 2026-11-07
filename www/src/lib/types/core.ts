// Documents, extraction, chat, live events

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
  rule_type: string;
  object: string;
  description?: string;
  [key: string]: unknown;
}

export interface DocumentIdentification {
  parts: { object: string; field: string }[];
  separator?: string;
}

export interface TemplateSchema {
  document_type?: string;
  description?: string;
  objects: TemplateObject[];
  /** Which one-per-document fields name a document to people. */
  identification?: DocumentIdentification;
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
  /** The identification key (e.g. "North Branch_2026-07-22") once the record has its values; show it before the file name. */
  display_name: string | null;
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
    errors?: { code: string; message: string; path?: string; rule?: string }[];
    /** Failures of rules set to warn only: reported, never blocking. */
    warnings?: { code: string; message: string; path?: string; rule?: string }[];
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

// The destination mapping preview (Connections > Publish) shares this name; the two declarations merge.
export interface MappingPreview {
  envelope: Record<string, unknown>;
  problems: string[];
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
  /** chat: what it is for, fixed by the page it was started from. */
  mode?: "general" | "rules" | "screens";
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
  /** Threads Syruss started that are still open (kind=agent and kind=syruss only). */
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
