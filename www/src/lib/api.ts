import type {
  Access, AgentQuestion, AgentRun, AgentRunDetail, MemoryItem, MemoryKind, ApiKeyInfo, ApiSourceInput, AppNotification, AuditEntry, Billing, ChatMessage, Conversation,
  DocumentItem, Entity, MyOrganization, Screen, ScreenCatalogue, ScreenDefinition, ScreenFilter, ScreenRun, ScreenStarter, ScreenSummary, Extraction, Invitation, InvitationPreview, KnowledgeSource, Me, Member, NotificationPref,
  InviteIssued, LogPage, OrgActivityPage, OrgOverview, Organization, Overview, SecurityEventPage, TrustedDevice, Proposal, RecordSet, ReviewTask, Role, RoleCatalogue, Template, TemplateSchema, TemplateVersion, OrgMemberInfo, OrgInvitationInfo, OrgRole, Permission,
  WebhookDeliveryInfo, WebhookInfo, Workspace, WorkspaceEventType, WorkspaceSettingsData, BudgetStatus, ReflectionState,
  IntakeActivityPage, IntakeChannelInfo, IntakeKeyInfo, DeveloperInfo, ChannelConfigPatch, NewIntakeKey,
  ConnectionsOverview, DeliveryDetail, DeliveryPage, DeliveryStats, DeliveryStatus, DestinationInfo, DestinationKind, MappingPreview, MappingSources, MappingSpec,
  NewDestination, TryResult,
  WorkspaceJobInfo, LiveQuality, GoldenDocument, EvalRunInfo, FlagAnswer, ConversationPage, SystemLesson,
} from "./types";

/**
 * Typed client for the core API. Workspace calls always name their workspace explicitly
 * (taken from the URL), never from browser storage.
 */

const API = "/api/v1";
const SITE = (process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3100").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; errors?: { message: string }[]; problems?: string[] };
    const first = d.errors?.[0]?.message ?? d.problems?.[0];
    if (d.message && first) return `${d.message} ${first}`;
    if (d.message) return d.message;
    if (first) return first;
  }
  return fallback;
}

/**
 * The chat you have open. Actions you take in the dashboard while it is open (uploads, processing,
 * data sources) are linked to it, so their progress shows in that chat. Set by the chat panel.
 */
let activeOrg: string | undefined;
/** The organization named in the address; every call names it so the API can check you belong to it. */
export const setActiveOrg = (slug: string | undefined) => {
  activeOrg = slug;
};

let activeConversation: string | undefined;
export const setActiveConversation = (id: string | undefined) => {
  activeConversation = id;
};
export const getActiveConversation = () => activeConversation;

async function request<T>(path: string, init: RequestInit & { workspaceId?: string } = {}): Promise<T> {
  const { workspaceId, headers, ...rest } = init;
  const changes = !!rest.method && rest.method !== "GET";
  const res = await fetch(`${API}${path}`, {
    ...rest,
    headers: {
      ...(rest.body && !(rest.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(activeOrg ? { "X-Org-Slug": activeOrg } : {}),
      ...(workspaceId ? { "X-Template-ID": workspaceId } : {}),
      ...(workspaceId && changes && activeConversation ? { "X-Conversation-ID": activeConversation } : {}),
      ...headers,
    },
  });
  if (res.status === 401 && typeof window !== "undefined") {
    // Signed out (or the session ended): sign in again and come back to this page.
    const here = window.location.pathname + window.location.search;
    window.location.assign(`${SITE}/api/auth/login?mode=signin&returnTo=${encodeURIComponent(`/continue?next=${encodeURIComponent(here)}`)}`);
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = undefined;
    }
    if (res.status === 403 && (detail as { code?: string } | undefined)?.code === "reauth_required" && typeof window !== "undefined") {
      // A risky action: sign in again (Keycloak asks for the password), then come back to this page to do it.
      window.location.assign(`/api/auth/login?reauth=1&returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    }
    if (res.status === 403 && (detail as { code?: string } | undefined)?.code === "device_unverified" && typeof window !== "undefined") {
      // A browser the account hasn't confirmed: the code page is on the site, and brings the person back here.
      const here = window.location.pathname + window.location.search;
      window.location.assign(`${SITE}/verify-device?next=${encodeURIComponent(`/continue?next=${encodeURIComponent(here)}`)}`);
    }
    throw new ApiError(res.status, messageFrom(detail, `Request failed (${res.status}).`), detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  screens: {
    list: (workspaceId: string) => request<ScreenSummary[]>("/screens", { workspaceId }),
    get: (workspaceId: string, slug: string) => request<Screen>(`/screens/${encodeURIComponent(slug)}`, { workspaceId }),
    catalogue: (workspaceId: string) => request<ScreenCatalogue>("/screens/catalogue", { workspaceId }),
    starters: (workspaceId: string) => request<ScreenStarter[]>("/screens/starters", { workspaceId }),
    create: (workspaceId: string, body: { name: string; description?: string; definition: ScreenDefinition }) =>
      request<Screen>("/screens", { method: "POST", body: json(body), workspaceId }),
    update: (workspaceId: string, slug: string, body: { name?: string; description?: string; definition?: ScreenDefinition }) =>
      request<Screen>(`/screens/${encodeURIComponent(slug)}`, { method: "PATCH", body: json(body), workspaceId }),
    remove: (workspaceId: string, slug: string) => request<void>(`/screens/${encodeURIComponent(slug)}`, { method: "DELETE", workspaceId }),
    run: (workspaceId: string, slug: string, body: { offset?: number; limit?: number; filters?: ScreenFilter[]; sort?: { column: string; dir: "asc" | "desc" }[]; search?: string }) =>
      request<ScreenRun>(`/screens/${encodeURIComponent(slug)}/run`, { method: "POST", body: json(body), workspaceId }),
    preview: (workspaceId: string, definition: ScreenDefinition, offset = 0, limit = 25) =>
      request<ScreenRun>("/screens/preview", { method: "POST", body: json({ definition, offset, limit }), workspaceId }),
    /** Asks Syruss to look through the records now and suggest screens. */
    lookForMore: (workspaceId: string) => request<{ status: string; records: number; waiting: number }>("/screens/insights/run", { method: "POST", workspaceId }),
    accept: (workspaceId: string, slug: string) => request<Screen>(`/screens/${encodeURIComponent(slug)}/accept`, { method: "POST", workspaceId }),
    dismiss: (workspaceId: string, slug: string) => request<void>(`/screens/${encodeURIComponent(slug)}/dismiss`, { method: "POST", workspaceId }),
    /** A download link can't send headers, so the workspace and organization ride in the address. */
    exportUrl: (workspaceId: string, slug: string) =>
      `${API}/screens/${encodeURIComponent(slug)}/export.csv?template_id=${workspaceId}${activeOrg ? `&org=${encodeURIComponent(activeOrg)}` : ""}`,
  },
  organization: () => request<Organization>("/organization"),

  /** The organization's people. Roles here run the organization; they never open a workspace's data. */
  org: {
    rename: (name: string) => request<Organization>("/organization", { method: "PATCH", body: json({ name }) }),
    overview: () => request<OrgOverview>("/organization/overview"),
    activity: (before?: string | null, limit = 30) =>
      request<OrgActivityPage>(`/organization/activity?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`),
    close: (confirm: string) => request<void>("/organization", { method: "DELETE", body: json({ confirm }) }),
    members: () => request<OrgMemberInfo[]>("/organization/members"),
    changeRole: (userId: string, role: OrgRole) =>
      request<OrgMemberInfo>(`/organization/members/${encodeURIComponent(userId)}`, { method: "PATCH", body: json({ role }) }),
    removeMember: (userId: string) => request<void>(`/organization/members/${encodeURIComponent(userId)}`, { method: "DELETE" }),
    invitations: () => request<OrgInvitationInfo[]>("/organization/invitations"),
    invite: (email: string, role: Exclude<OrgRole, "owner">, note?: string) =>
      request<InviteIssued>("/organization/invitations", { method: "POST", body: json({ email, role, note: note || undefined }) }),
    renewInvitation: (id: string) => request<InviteIssued>(`/organization/invitations/${id}/renew`, { method: "POST" }),
    resendInvitation: (id: string) => request<InviteIssued>(`/organization/invitations/${id}/resend`, { method: "POST" }),
    revokeInvitation: (id: string) => request<void>(`/organization/invitations/${id}`, { method: "DELETE" }),
  },

  workspaces: {
    list: () => request<Workspace[]>("/workspaces"),
    get: (slug: string) => request<Workspace>(`/workspaces/${encodeURIComponent(slug)}`),
    create: (name: string, description?: string) =>
      request<Workspace>("/workspaces", { method: "POST", body: json({ name, description }) }),
    update: (slug: string, body: { name?: string; description?: string }) =>
      request<Workspace>(`/workspaces/${encodeURIComponent(slug)}`, { method: "PATCH", body: json(body) }),
    /** An organization admin adds themselves to a workspace to open its data (recorded in its audit log). */
    join: (slug: string, role = "admin") =>
      request<Workspace>(`/workspaces/${encodeURIComponent(slug)}/join`, { method: "POST", body: json({ role }) }),
    /** Deletes the workspace you own (its "delete the workspace" privilege). */
    removeOwn: (workspaceId: string) => request<void>(`/templates/${workspaceId}`, { method: "DELETE", workspaceId }),
    /** Deletes the workspace and everything in it (organization owners and admins). */
    remove: (slug: string) => request<void>(`/workspaces/${encodeURIComponent(slug)}`, { method: "DELETE" }),
  },

  template: {
    get: (workspaceId: string) => request<Template>(`/templates/${workspaceId}`, { workspaceId }),
    versions: (workspaceId: string) => request<TemplateVersion[]>(`/templates/${workspaceId}/versions`, { workspaceId }),
    /** Uploads samples (or none, to study the existing ones again); the agent proposes a template. */
    discover: (workspaceId: string, files: File[], notes: string, threadId: string) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      fd.append("template_id", workspaceId);
      fd.append("session_id", threadId);
      if (notes.trim()) fd.append("user_notes", notes.trim());
      return request<{ template_id: string }>("/templates/discover", { method: "POST", body: fd, workspaceId });
    },
    /** Sets the agent's pending proposal aside to start the design over. */
    withdrawProposal: (workspaceId: string) =>
      request<{ proposals: number; questions_closed: number }>(`/templates/${workspaceId}/proposal/withdraw`, { method: "POST", workspaceId }),
    /** Publishes the schema as the active version (first publish or a direct edit). */
    publish: (workspaceId: string, name: string, schema: TemplateSchema, isFirst: boolean) =>
      isFirst
        ? request<Template>("/templates", { method: "POST", body: json({ id: workspaceId, name, schema_definition: schema }), workspaceId })
        : request<Template>(`/templates/${workspaceId}`, { method: "PUT", body: json({ schema_definition: schema }), workspaceId }),
  },

  log: {
    list: (workspaceId: string, opts: { before?: number | null; level?: string; documentId?: string; limit?: number } = {}) => {
      const q = new URLSearchParams({ limit: String(opts.limit ?? 40) });
      if (opts.before) q.set("before", String(opts.before));
      if (opts.level && opts.level !== "all") q.set("level", opts.level);
      if (opts.documentId) q.set("document_id", opts.documentId);
      return request<LogPage>(`/extraction-log?${q}`, { workspaceId });
    },
  },
  overview: (workspaceId: string, days = 14) => request<Overview>(`/overview?days=${days}`, { workspaceId }),
  documents: {
    list: (workspaceId: string, status?: string, limit = 100) =>
      request<DocumentItem[]>(`/documents?limit=${limit}${status ? `&status=${status}` : ""}`, { workspaceId }),
    samples: (workspaceId: string) => request<DocumentItem[]>("/documents?role=SAMPLE&limit=20", { workspaceId }),
    get: (workspaceId: string, id: string) => request<DocumentItem>(`/documents/${id}`, { workspaceId }),
    upload: (workspaceId: string, file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return request<DocumentItem>("/documents/upload", { method: "POST", body: fd, workspaceId });
    },
    /** A person corrects a saved record; their changes and reasons go to Syruss in the open chat (or a new one). */
    correct: (workspaceId: string, id: string, data: Extraction["extracted_data"], reasons: { object: string; reason: string }[]) =>
      request<{ status: string; conversation_id: string; message_id: string }>(`/documents/${id}/corrections`, {
        method: "POST", workspaceId, body: json({ data, reasons }),
      }),
    reprocess: (workspaceId: string, id: string) =>
      request<{ status: string }>(`/documents/${id}/process`, { method: "POST", workspaceId }),
    // An <img> can't send headers, so the organization rides in the address; the app's proxy turns it into X-Org-Slug.
    fileUrl: (workspaceId: string, id: string) =>
      `${API}/documents/${id}/file?template_id=${workspaceId}${activeOrg ? `&org=${encodeURIComponent(activeOrg)}` : ""}`,
    extraction: (workspaceId: string, id: string) => request<Extraction>(`/extractions/${id}`, { workspaceId }),
  },

  reviews: {
    list: (workspaceId: string, decided = false) => request<ReviewTask[]>(`/reviews${decided ? "?decided=true" : ""}`, { workspaceId }),
    decide: (workspaceId: string, id: string, action: "APPROVE" | "CORRECT" | "REJECT", correctedData?: unknown, notes?: string) =>
      request<ReviewTask>(`/reviews/${id}/decide`, {
        method: "POST", workspaceId, body: json({ action, corrected_data: correctedData, notes }),
      }),
  },

  questions: {
    list: (workspaceId: string, status = "OPEN") => request<AgentQuestion[]>(`/questions?status=${status}`, { workspaceId }),
    answer: (workspaceId: string, id: string, answer: FlagAnswer) =>
      request<AgentQuestion>(`/questions/${id}/answer`, { method: "POST", workspaceId, body: json(answer) }),
    dismiss: (workspaceId: string, id: string, reason?: string) =>
      request<AgentQuestion>(`/questions/${id}/dismiss`, { method: "POST", workspaceId, body: json({ reason }) }),
  },

  proposals: {
    list: (workspaceId: string, status = "PENDING") =>
      request<Proposal[]>(`/knowledge/proposals?status=${status}`, { workspaceId }),
    review: (workspaceId: string, id: string, action: "APPROVE" | "REJECT", notes?: string) =>
      request<Proposal>(`/knowledge/proposals/${id}/review`, { method: "POST", workspaceId, body: json({ action, notes }) }),
  },

  entities: {
    list: (workspaceId: string, query?: string) =>
      request<Entity[]>(`/knowledge/entities?limit=200${query ? `&query=${encodeURIComponent(query)}` : ""}`, { workspaceId }),
    create: (workspaceId: string, body: { entity_type: string; name: string; identifier_key?: string; identifier_value?: string }) =>
      request<Entity>("/knowledge/entities", { method: "POST", workspaceId, body: json(body) }),
  },

  /** Reference data (files, APIs) the agent maps onto the template; imported once you approve. */
  sources: {
    list: (workspaceId: string) => request<KnowledgeSource[]>("/knowledge/sources", { workspaceId }),
    addFile: (workspaceId: string, file: File, notes: string) => {
      const fd = new FormData();
      fd.append("file", file);
      if (notes.trim()) fd.append("notes", notes.trim());
      return request<KnowledgeSource>("/knowledge/sources/file", { method: "POST", body: fd, workspaceId });
    },
    testApi: (workspaceId: string, body: ApiSourceInput, sourceId?: string) =>
      request<{ record_sets: RecordSet[] }>(`/knowledge/sources/api/test${sourceId ? `?source_id=${sourceId}` : ""}`, {
        method: "POST", body: json(body), workspaceId,
      }),
    addApi: (workspaceId: string, body: ApiSourceInput) =>
      request<KnowledgeSource>("/knowledge/sources/api", { method: "POST", body: json(body), workspaceId }),
    updateApi: (workspaceId: string, id: string, body: ApiSourceInput) =>
      request<KnowledgeSource>(`/knowledge/sources/${id}`, { method: "PUT", body: json(body), workspaceId }),
    sync: (workspaceId: string, id: string) =>
      request<{ source: KnowledgeSource; imported: { created: number; updated: number } | null }>(`/knowledge/sources/${id}/sync`, {
        method: "POST", workspaceId,
      }),
    remap: (workspaceId: string, id: string, notes: string) =>
      request<KnowledgeSource>(`/knowledge/sources/${id}/remap`, { method: "POST", body: json({ notes }), workspaceId }),
    remove: (workspaceId: string, id: string) => request<void>(`/knowledge/sources/${id}`, { method: "DELETE", workspaceId }),
  },

  /** Workspace settings. Every call checks your role in the workspace. */
  settings: {
    get: (workspaceId: string) => request<{ settings: WorkspaceSettingsData; access: Access }>("/settings", { workspaceId }),
    update: (workspaceId: string, patch: Partial<{ [K in keyof WorkspaceSettingsData]: Partial<WorkspaceSettingsData[K]> }>) =>
      request<{ settings: WorkspaceSettingsData; access: Access }>("/settings", { method: "PATCH", body: json(patch), workspaceId }),
    billing: (workspaceId: string) => request<Billing>("/settings/billing", { workspaceId }),
    budget: (workspaceId: string) => request<BudgetStatus>("/settings/budget", { workspaceId }),
    reflection: (workspaceId: string) => request<ReflectionState>("/settings/reflection", { workspaceId }),
    reflectNow: (workspaceId: string) =>
      request<{ reflect: WorkspaceJobInfo; result: { status: string; retired: number; merged: number } }>("/settings/reflection/run", { method: "POST", workspaceId }),
    roles: (workspaceId: string) => request<RoleCatalogue>("/settings/roles", { workspaceId }),
    createRole: (workspaceId: string, body: { name: string; description: string; permissions: Permission[] }) =>
      request<unknown>("/settings/roles", { method: "POST", body: json(body), workspaceId }),
    updateRole: (workspaceId: string, id: string, body: { name?: string; description?: string; permissions?: Permission[] }) =>
      request<unknown>(`/settings/roles/${id}`, { method: "PATCH", body: json(body), workspaceId }),
    deleteRole: (workspaceId: string, id: string) => request<void>(`/settings/roles/${id}`, { method: "DELETE", workspaceId }),
    members: (workspaceId: string) => request<Member[]>("/settings/members", { workspaceId }),
    changeRole: (workspaceId: string, memberId: string, role: Role) =>
      request<Member>(`/settings/members/${memberId}`, { method: "PATCH", body: json({ role }), workspaceId }),
    removeMember: (workspaceId: string, memberId: string) =>
      request<void>(`/settings/members/${memberId}`, { method: "DELETE", workspaceId }),
    myNotifications: (workspaceId: string) => request<NotificationPref[]>("/settings/notifications", { workspaceId }),
    setMyNotifications: (workspaceId: string, prefs: Record<string, boolean>) =>
      request<NotificationPref[]>("/settings/notifications", { method: "PUT", body: json(prefs), workspaceId }),
    invitations: (workspaceId: string) => request<Invitation[]>("/settings/invitations", { workspaceId }),
    invite: (workspaceId: string, email: string, role: Role, note?: string) =>
      request<InviteIssued>("/settings/invitations", { method: "POST", body: json({ email, role, note: note || undefined }), workspaceId }),
    renewInvitation: (workspaceId: string, id: string) =>
      request<InviteIssued>(`/settings/invitations/${id}/renew`, { method: "POST", workspaceId }),
    resendInvitation: (workspaceId: string, id: string) =>
      request<InviteIssued>(`/settings/invitations/${id}/resend`, { method: "POST", workspaceId }),
    revokeInvitation: (workspaceId: string, id: string) =>
      request<void>(`/settings/invitations/${id}`, { method: "DELETE", workspaceId }),
    apiKeys: (workspaceId: string) => request<ApiKeyInfo[]>("/settings/api-keys", { workspaceId }),
    createApiKey: (workspaceId: string, body: { name: string; scope: "read" | "write"; expires_in_days?: number | null }) =>
      request<{ api_key: ApiKeyInfo; key: string }>("/settings/api-keys", { method: "POST", body: json(body), workspaceId }),
    revokeApiKey: (workspaceId: string, id: string) => request<void>(`/settings/api-keys/${id}`, { method: "DELETE", workspaceId }),
    webhooks: (workspaceId: string) => request<{ webhooks: WebhookInfo[]; events: WorkspaceEventType[] }>("/settings/webhooks", { workspaceId }),
    createWebhook: (workspaceId: string, body: { url: string; description: string; events: string[] }) =>
      request<{ webhook: WebhookInfo; secret: string }>("/settings/webhooks", { method: "POST", body: json(body), workspaceId }),
    updateWebhook: (workspaceId: string, id: string, patch: Partial<Pick<WebhookInfo, "url" | "description" | "events" | "active">>) =>
      request<WebhookInfo>(`/settings/webhooks/${id}`, { method: "PATCH", body: json(patch), workspaceId }),
    deleteWebhook: (workspaceId: string, id: string) => request<void>(`/settings/webhooks/${id}`, { method: "DELETE", workspaceId }),
    testWebhook: (workspaceId: string, id: string) =>
      request<{ ok: boolean; status_code: number | null; error: string | null; duration_ms: number }>(`/settings/webhooks/${id}/test`, { method: "POST", workspaceId }),
    rotateWebhookSecret: (workspaceId: string, id: string) =>
      request<{ secret: string }>(`/settings/webhooks/${id}/rotate-secret`, { method: "POST", workspaceId }),
    deliveries: (workspaceId: string, id: string) => request<WebhookDeliveryInfo[]>(`/settings/webhooks/${id}/deliveries`, { workspaceId }),
    audit: (workspaceId: string, limit = 50, offset = 0) => request<AuditEntry[]>(`/audit?limit=${limit}&offset=${offset}`, { workspaceId }),
  },

  runs: {
    list: (workspaceId: string, filters: { origin?: string; source?: string; status?: string; before?: string; mine?: boolean; limit?: number } = {}) => {
      const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v).map(([k, v]) => [k, String(v)]));
      return request<{ items: AgentRun[]; has_more: boolean; scope: "all" | "mine" }>(`/runs?${q}`, { workspaceId });
    },
    get: (workspaceId: string, id: string) => request<AgentRunDetail>(`/runs/${id}`, { workspaceId }),
  },

  /** How Syruss works: its platform-wide lessons (admins approve what it proposes). */
  systemLessons: {
    list: () => request<{ items: SystemLesson[]; can_manage: boolean }>("/system-lessons"),
    add: (content: string) => request<SystemLesson>("/system-lessons", { method: "POST", body: json({ content }) }),
    update: (id: string, patch: { status?: SystemLesson["status"]; content?: string }) =>
      request<SystemLesson>(`/system-lessons/${id}`, { method: "PATCH", body: json(patch) }),
  },

  /** How well the agent reads: live figures, golden documents and shadow evaluations. */
  quality: {
    live: (workspaceId: string, days = 30) => request<LiveQuality>(`/quality?days=${days}`, { workspaceId }),
    golden: (workspaceId: string) => request<GoldenDocument[]>("/quality/golden", { workspaceId }),
    addGolden: (workspaceId: string, documentId: string) =>
      request<{ id: string }>("/quality/golden", { method: "POST", body: json({ document_id: documentId }), workspaceId }),
    removeGolden: (workspaceId: string, id: string) => request<void>(`/quality/golden/${id}`, { method: "DELETE", workspaceId }),
    evals: (workspaceId: string) => request<EvalRunInfo[]>("/quality/evals", { workspaceId }),
    evaluation: (workspaceId: string, id: string) => request<EvalRunInfo>(`/quality/evals/${id}`, { workspaceId }),
    startEvaluation: (workspaceId: string) => request<EvalRunInfo>("/quality/evals", { method: "POST", workspaceId }),
    cancelEvaluation: (workspaceId: string, id: string) => request<EvalRunInfo>(`/quality/evals/${id}/cancel`, { method: "POST", workspaceId }),
  },

  /** What Syruss remembers: about you (private) and about the workspace (shared). */
  memories: {
    list: (workspaceId: string, scope: "personal" | "workspace", filters: { kind?: string; q?: string } = {}) => {
      const q = new URLSearchParams({ scope, ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) });
      return request<{ items: MemoryItem[]; counts: { personal: number; workspace: number }; can_manage_workspace: boolean; semantic_search: boolean }>(
        `/memories?${q}`, { workspaceId },
      );
    },
    teach: (workspaceId: string, body: { scope: "personal" | "workspace"; kind: MemoryKind; content: string }) =>
      request<MemoryItem>("/memories", { method: "POST", body: json(body), workspaceId }),
    update: (workspaceId: string, id: string, patch: { content?: string; pinned?: boolean; archived?: boolean }) =>
      request<MemoryItem>(`/memories/${id}`, { method: "PATCH", body: json(patch), workspaceId }),
    remove: (workspaceId: string, id: string) => request<void>(`/memories/${id}`, { method: "DELETE", workspaceId }),
    forgetMe: (workspaceId: string) => request<void>("/memories?scope=personal", { method: "DELETE", workspaceId }),
  },

  me: {
    get: () => request<Me>("/me"),
    /** Every organization you belong to (not only the one named in the address). */
    organizations: () => request<{ organizations: MyOrganization[] }>("/me/context").then((c) => c.organizations),
    devices: () => request<TrustedDevice[]>("/me/devices"),
    removeDevice: (id: string) => request<void>(`/me/devices/${id}`, { method: "DELETE" }),
    removeOtherDevices: () => request<{ removed: number }>("/me/devices/revoke-others", { method: "POST" }),
    securityEvents: (before?: string | null) =>
      request<SecurityEventPage>(`/me/security-events?limit=30${before ? `&before=${encodeURIComponent(before)}` : ""}`),
    /** Your profile. The email belongs to your sign-in and can't be changed here. */
    update: (body: { first_name?: string; last_name?: string; phone?: string; job_title?: string; timezone?: string }) =>
      request<Me>("/me", { method: "PATCH", body: json(body) }),
  },

  invitations: {
    view: (token: string) => request<InvitationPreview>(`/invitations/${encodeURIComponent(token)}`),
    accept: (token: string) =>
      request<{ workspace: { id: string; slug: string }; role: Role }>(`/invitations/${encodeURIComponent(token)}/accept`, { method: "POST" }),
  },

  notifications: {
    list: (opts: { unreadOnly?: boolean; before?: string; limit?: number } = {}) => {
      const q = new URLSearchParams();
      if (opts.unreadOnly) q.set("unread_only", "true");
      if (opts.before) q.set("before", opts.before);
      q.set("limit", String(opts.limit ?? 30));
      return request<{ items: AppNotification[]; unread: number; has_more: boolean }>(`/notifications?${q}`);
    },
    setRead: (id: string, read = true) => request<AppNotification>(`/notifications/${id}/read`, { method: "POST", body: json({ read }) }),
    readAll: () => request<void>("/notifications/read-all", { method: "POST" }),
    remove: (id: string) => request<void>(`/notifications/${id}`, { method: "DELETE" }),
    clear: (which: "all" | "read" = "all") => request<void>(`/notifications?which=${which}`, { method: "DELETE" }),
  },

  /** Your private chats with Syruss in a workspace. */
  /** Receiving documents from other systems: channels, the keys each sender uses, and what arrived. */
  channels: {
    list: (workspaceId: string) => request<IntakeChannelInfo[]>("/channels", { workspaceId }),
    developerInfo: (workspaceId: string) => request<DeveloperInfo>("/channels/developer-info", { workspaceId }),
    create: (workspaceId: string, body: { name: string; kind?: "api" | "email" | "sftp"; config?: ChannelConfigPatch }) =>
      request<IntakeChannelInfo & { sftp_password?: string }>("/channels", { method: "POST", body: json(body), workspaceId }),
    resetSftpPassword: (workspaceId: string, id: string) => request<{ password: string }>(`/channels/${id}/sftp/password`, { method: "POST", workspaceId }),
    setSftpKey: (workspaceId: string, id: string, publicKey: string) =>
      request<IntakeChannelInfo>(`/channels/${id}/sftp/public-key`, { method: "PUT", body: json({ public_key: publicKey }), workspaceId }),
    update: (workspaceId: string, id: string, body: { name?: string; status?: "active" | "paused"; config?: ChannelConfigPatch }) =>
      request<IntakeChannelInfo>(`/channels/${id}`, { method: "PATCH", body: json(body), workspaceId }),
    remove: (workspaceId: string, id: string) => request<void>(`/channels/${id}`, { method: "DELETE", workspaceId }),
    createKey: (workspaceId: string, id: string, body: NewIntakeKey) =>
      request<{ key: IntakeKeyInfo; secret: string }>(`/channels/${id}/keys`, { method: "POST", body: json(body), workspaceId }),
    rotateKey: (workspaceId: string, id: string, keyId: string, overlapHours: number) =>
      request<{ key: IntakeKeyInfo; secret: string; old_key: IntakeKeyInfo }>(`/channels/${id}/keys/${keyId}/rotate`, {
        method: "POST", body: json({ overlap_hours: overlapHours }), workspaceId,
      }),
    revokeKey: (workspaceId: string, id: string, keyId: string) => request<void>(`/channels/${id}/keys/${keyId}`, { method: "DELETE", workspaceId }),
    activity: (workspaceId: string, id: string, opts: { outcome?: "accepted" | "rejected"; before?: number | null } = {}) => {
      const q = new URLSearchParams({ limit: "30" });
      if (opts.outcome) q.set("outcome", opts.outcome);
      if (opts.before) q.set("before", String(opts.before));
      return request<IntakeActivityPage>(`/channels/${id}/activity?${q}`, { workspaceId });
    },
    testDocument: (workspaceId: string, id: string) =>
      request<{ document_id: string; status: string }>(`/channels/${id}/test-document`, { method: "POST", body: json({ process: false }), workspaceId }),
  },
  /** Publishing approved records to other systems. */
  destinations: {
    catalogue: (workspaceId: string) => request<DestinationKind[]>("/destinations/catalogue", { workspaceId }),
    sources: (workspaceId: string) => request<MappingSources>("/destinations/sources", { workspaceId }),
    list: (workspaceId: string) => request<DestinationInfo[]>("/destinations", { workspaceId }),
    get: (workspaceId: string, id: string) => request<DestinationInfo>(`/destinations/${id}`, { workspaceId }),
    create: (workspaceId: string, body: NewDestination) => request<DestinationInfo>("/destinations", { method: "POST", body: json(body), workspaceId }),
    update: (workspaceId: string, id: string, body: Partial<Pick<NewDestination, "name" | "config" | "secrets" | "mapping" | "policy">>) =>
      request<DestinationInfo>(`/destinations/${id}`, { method: "PATCH", body: json(body), workspaceId }),
    remove: (workspaceId: string, id: string) => request<void>(`/destinations/${id}`, { method: "DELETE", workspaceId }),
    pause: (workspaceId: string, id: string) => request<DestinationInfo>(`/destinations/${id}/pause`, { method: "POST", workspaceId }),
    resume: (workspaceId: string, id: string) => request<DestinationInfo>(`/destinations/${id}/resume`, { method: "POST", workspaceId }),
    rotateSecret: (workspaceId: string, id: string, overlapHours: number) =>
      request<{ signing_secret: string; overlap_hours: number }>(`/destinations/${id}/rotate-secret`, { method: "POST", body: json({ overlap_hours: overlapHours }), workspaceId }),
    tryConnection: (workspaceId: string, body: { kind: string; config: Record<string, unknown>; secrets: Record<string, string>; destination_id?: string }) =>
      request<TryResult>("/destinations/try", { method: "POST", body: json(body), workspaceId }),
    oauthStart: (workspaceId: string, id: string) => request<{ url: string }>(`/destinations/${id}/oauth/start`, { method: "POST", workspaceId }),
    suggestMapping: (workspaceId: string, kind: string) => request<MappingSpec>("/destinations/suggest", { method: "POST", body: json({ kind }), workspaceId }),
    preview: (workspaceId: string, body: { kind: string; mapping: MappingSpec; document_id: string }) =>
      request<MappingPreview>("/destinations/preview", { method: "POST", body: json(body), workspaceId }),
  },
  deliveries: {
    list: (workspaceId: string, opts: { status?: DeliveryStatus[]; destinationId?: string; documentId?: string; q?: string; offset?: number; limit?: number } = {}) => {
      const q = new URLSearchParams({ limit: String(opts.limit ?? 30), offset: String(opts.offset ?? 0) });
      if (opts.status?.length) q.set("status", opts.status.join(","));
      if (opts.destinationId) q.set("destination_id", opts.destinationId);
      if (opts.documentId) q.set("document_id", opts.documentId);
      if (opts.q?.trim()) q.set("q", opts.q.trim());
      return request<DeliveryPage>(`/deliveries?${q}`, { workspaceId });
    },
    stats: (workspaceId: string) => request<DeliveryStats>("/deliveries/stats", { workspaceId }),
    get: (workspaceId: string, id: string) => request<DeliveryDetail>(`/deliveries/${id}`, { workspaceId }),
    replay: (workspaceId: string, id: string) => request<{ id: string; status: string }>(`/deliveries/${id}/replay`, { method: "POST", workspaceId }),
    release: (workspaceId: string, id: string) => request<{ id: string; status: string }>(`/deliveries/${id}/release`, { method: "POST", workspaceId }),
    cancel: (workspaceId: string, id: string) => request<{ id: string; status: string }>(`/deliveries/${id}/cancel`, { method: "POST", workspaceId }),
    replayMany: (workspaceId: string, body: { status?: "dead" | "cancelled"; destination_id?: string; ids?: string[] }) =>
      request<{ replayed: number }>("/deliveries/replay", { method: "POST", body: json(body), workspaceId }),
  },
  connections: {
    overview: (workspaceId: string) => request<ConnectionsOverview>("/connections/overview", { workspaceId }),
  },
  chat: {
    /** A page of conversation titles (never messages): your chats, or Syruss's conversations with your role. */
    list: (workspaceId: string, opts: { kind?: "chat" | "agent" | "history"; offset?: number; limit?: number; status?: "open" | "resolved" } = {}) => {
      const q = new URLSearchParams({ kind: opts.kind ?? "chat", offset: String(opts.offset ?? 0), limit: String(opts.limit ?? 20) });
      if (opts.status) q.set("status", opts.status);
      return request<ConversationPage>(`/conversations?${q}`, { workspaceId });
    },
    get: (workspaceId: string, id: string) => request<Conversation>(`/conversations/${id}`, { workspaceId }),
    create: (workspaceId: string, title = "New chat") =>
      request<Conversation>("/conversations", { method: "POST", body: json({ title }), workspaceId }),
    rename: (workspaceId: string, id: string, title: string) =>
      request<Conversation>(`/conversations/${id}`, { method: "PATCH", body: json({ title }), workspaceId }),
    remove: (workspaceId: string, id: string) => request<void>(`/conversations/${id}`, { method: "DELETE", workspaceId }),
    /** Stops Syruss's reply in this conversation (queued or under way). */
    stop: (workspaceId: string, id: string) => request<{ stopped: number }>(`/conversations/${id}/stop`, { method: "POST", workspaceId }),
    send: (workspaceId: string, conversationId: string, content: string, context?: Record<string, unknown>) =>
      request<ChatMessage>(`/conversations/${conversationId}/messages?wait=false`, {
        method: "POST", workspaceId, body: json({ content, context }),
      }),
  },
};

/**
 * The address of a live channel (Server-Sent Events). It goes through this app like every other request, so it is
 * signed in the same way and the API checks you may listen, now and while it stays open. An EventSource can't set
 * headers, so the organization rides in the address.
 */
export function liveStreamUrl(channel: string): string {
  return `${API}/live/${encodeURIComponent(channel)}${activeOrg ? `?org=${encodeURIComponent(activeOrg)}` : ""}`;
}
