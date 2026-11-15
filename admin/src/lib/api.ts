import type { SearchAnswer } from "@/components/shell/CommandPalette";
import type {
  BankAccountInfo, BillingPayment, BillingSettingsPage, BillingSetup, CatalogDraft, CatalogItem, CatalogKind, CreditNoteInfo, FollowUp, MoneySummary, OrganizationAccount, PersonAccount,
  Announcement, AtRisk, BusinessMetrics, OrgNote, OrganizationProfile, PersonProfile, PlatformControls, SecurityView, Severity, SwitchName,
  Invoice, OrgBilling, OrgBillingRow, PaymentFileInfo, SellerSettings, Submission, TaxRateInfo,
  Names, PlatformHealth, PlatformStats, ProcessDetail, ProcessList, QueuesPage, RunDetail, RunList, RunStats,
  StaffActionPage, StaffInvitation, StaffList, StaffMe, StaffMember, StaffRole, JoinView, SystemLesson,
  Approval, ApprovalList, ApprovalRule, StaffPolicy,
  ObjectProfile, ObjectSummary, Paged, ProfileWhere, ThingProfile, ThingSummary,
  LiveChat, LiveMessage, LiveQueue, MailImapInput, MailJob, MailOverview, MailPurpose, Presence, SavedReply, SupportSettings, SupportSummary, Ticket, TicketDetail, TicketMessage,
} from "./types";
import { loadPage } from "./navigate";

/** Typed client for the platform tools (/api/v1/sysadmin), through this app's own proxy. */

const API = "/api/v1/sysadmin";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

/** A risky action the platform's rule holds for a second owner: nothing changed yet. */
export class ApprovalPending extends ApiError {
  constructor(message: string, public approval: Approval) {
    super(202, message, "approval_pending");
  }
}

/** Pages listening for held actions (the Approvals count) hear of each one. */
export const APPROVAL_EVENT = "syruss:approval-requested";

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return fallback;
}

const here = () => window.location.pathname + window.location.search;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, {
      ...init,
      headers: { ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}), ...init.headers },
    });
  } catch {
    // No answer at all: this browser is offline, or the admin app's own server is down.
    throw new ApiError(0, "Can't reach Syruss. Check your connection and try again.");
  }
  if (res.status === 401 && typeof window !== "undefined") {
    // Signed out, or the session ended: sign in again (with the one-time code) and come back to this page.
    loadPage(`/api/auth/login?returnTo=${encodeURIComponent(here())}`);
  }
  if (res.status === 202) {
    const body = (await res.json().catch(() => ({}))) as { approval?: Approval; message?: string };
    if (body.approval) {
      if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(APPROVAL_EVENT));
      throw new ApprovalPending(body.message ?? "Sent to another platform owner to approve.", body.approval);
    }
    return body as T;
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      detail = undefined;
    }
    const code = (detail as { code?: string } | undefined)?.code;
    if (res.status === 403 && code === "reauth_required" && typeof window !== "undefined") {
      // A platform change: sign in again first, then come back to this page to make it.
      loadPage(`/api/auth/login?reauth=1&returnTo=${encodeURIComponent(here())}`);
    }
    throw new ApiError(res.status, messageFrom(detail, `Request failed (${res.status}).`), code);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  me: () => request<StaffMe>("/me"),
  stats: () => request<PlatformStats>("/stats"),
  health: () => request<PlatformHealth>("/health"),
  staff: {
    list: () => request<StaffList>("/staff"),
    invitations: () => request<StaffInvitation[]>("/staff/invitations"),
    revokeInvitation: (id: string) => request<void>(`/staff/invitations/${encodeURIComponent(id)}`, { method: "DELETE" }),
    viewJoin: (token: string) => request<JoinView>(`/staff/join/${encodeURIComponent(token)}`),
    sendJoinCode: (token: string) => request<unknown>(`/staff/join/${encodeURIComponent(token)}/verify/send`, { method: "POST" }),
    confirmJoinCode: (token: string, code: string) =>
      request<unknown>(`/staff/join/${encodeURIComponent(token)}/verify`, { method: "POST", body: json({ code }) }),
    acceptJoin: (token: string, deleteCustomerAccount: boolean) =>
      request<{ role: StaffRole }>(`/staff/join/${encodeURIComponent(token)}`, { method: "POST", body: json({ delete_customer_account: deleteCustomerAccount }) }),
    changeRole: (userId: string, body: { role: StaffRole; reason?: string }) =>
      request<StaffMember>(`/staff/${encodeURIComponent(userId)}`, { method: "PATCH", body: json(body) }),
    remove: (userId: string, reason?: string) =>
      request<void>(`/staff/${encodeURIComponent(userId)}${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`, { method: "DELETE" }),
    review: (userId: string, note?: string) =>
      request<StaffMember>(`/staff/${encodeURIComponent(userId)}/review`, { method: "POST", body: json({ note: note || null }) }),
    setPolicy: (body: StaffPolicy & { reason: string }) => request<StaffPolicy>("/staff/policy", { method: "PUT", body: json(body) }),
  },
  support: {
    summary: () => request<SupportSummary>("/support/summary"),
    tickets: (params: Record<string, string | undefined>) => request<{ items: Ticket[] }>(`/support/tickets?${query(params)}`),
    ticket: (id: string) => request<TicketDetail>(`/support/tickets/${encodeURIComponent(id)}`),
    answer: (id: string, body: { body: string; internal: boolean; then?: "pending" | "solved"; files: File[] }) => {
      const form = new FormData();
      form.set("body", body.body);
      form.set("internal", String(body.internal));
      if (body.then) form.set("then", body.then);
      body.files.forEach((f) => form.append("files", f));
      return request<TicketMessage>(`/support/tickets/${encodeURIComponent(id)}/messages`, { method: "POST", body: form });
    },
    change: (id: string, body: { status?: string; priority?: string; category?: string; assignee_id?: string }) =>
      request<Ticket>(`/support/tickets/${encodeURIComponent(id)}`, { method: "PATCH", body: json(body) }),
    merge: (id: string, into: string) => request<Ticket>(`/support/tickets/${encodeURIComponent(id)}/merge`, { method: "POST", body: json({ into }) }),
    fileUrl: (ticketId: string, fileId: string) => `${API}/support/tickets/${encodeURIComponent(ticketId)}/files/${encodeURIComponent(fileId)}`,
    staff: () => request<{ id: string; name: string }[]>("/support/staff"),
    savedReplies: () => request<SavedReply[]>("/support/saved-replies"),
    addSavedReply: (body: { title: string; body: string; shortcut?: string | null }) =>
      request<SavedReply>("/support/saved-replies", { method: "POST", body: json(body) }),
    editSavedReply: (id: string, body: { title: string; body: string; shortcut?: string | null }) =>
      request<SavedReply>(`/support/saved-replies/${encodeURIComponent(id)}`, { method: "PATCH", body: json(body) }),
    removeSavedReply: (id: string) => request<void>(`/support/saved-replies/${encodeURIComponent(id)}`, { method: "DELETE" }),
    settings: () => request<SupportSettings>("/support/settings"),
    setSettings: (body: Omit<SupportSettings, "mailbox"> & { reason: string }) =>
      request<unknown>("/support/settings", { method: "PUT", body: json(body) }),
    live: {
      presence: () => request<{ status: Presence }>("/support/live/presence"),
      setPresence: (status: Presence) => request<{ status: Presence }>("/support/live/presence", { method: "PUT", body: json({ status }) }),
      queue: () => request<LiveQueue>("/support/live/queue"),
      accept: (id: string) => request<LiveChat>(`/support/live/chats/${encodeURIComponent(id)}/accept`, { method: "POST" }),
      chat: (id: string) => request<LiveChat>(`/support/live/chats/${encodeURIComponent(id)}`),
      say: (id: string, body: string) => request<LiveMessage>(`/support/live/chats/${encodeURIComponent(id)}/messages`, { method: "POST", body: json({ body }) }),
      typing: (id: string) => request<void>(`/support/live/chats/${encodeURIComponent(id)}/typing`, { method: "POST" }),
      transfer: (id: string, to: string) => request<unknown>(`/support/live/chats/${encodeURIComponent(id)}/transfer`, { method: "POST", body: json({ to }) }),
      ticket: (id: string) => request<{ id: string; number: string }>(`/support/live/chats/${encodeURIComponent(id)}/ticket`, { method: "POST" }),
      end: (id: string) => request<unknown>(`/support/live/chats/${encodeURIComponent(id)}/end`, { method: "POST" }),
    },
  },
  mail: {
    overview: () => request<MailOverview>("/mail/overview"),
    jobs: (status?: string) => request<MailJob[]>(`/mail/jobs?${query({ status, limit: "100" })}`),
    retry: (id: string) => request<unknown>(`/mail/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
    addImap: (body: MailImapInput) => request<unknown>("/mail/accounts/imap", { method: "POST", body: json(body) }),
    test: (id: string, purpose?: MailPurpose) =>
      request<{ ok: boolean; detail: string }>(`/mail/accounts/${encodeURIComponent(id)}/test?${query({ purpose })}`, { method: "POST" }),
    remove: (id: string) => request<void>(`/mail/accounts/${encodeURIComponent(id)}`, { method: "DELETE" }),
    route: (purpose: MailPurpose, accountId: string | null) =>
      request<unknown>(`/mail/routes/${purpose}`, { method: "PUT", body: json({ account_id: accountId }) }),
    googleStart: (purposes: MailPurpose[]) => request<{ url: string }>("/mail/google/start", { method: "POST", body: json({ purposes }) }),
    googleFinish: (code: string, state: string) =>
      request<{ id: string; address: string; granted: string[]; purposes: MailPurpose[] }>("/mail/google/finish", { method: "POST", body: json({ code, state }) }),
  },
  approvals: {
    list: (status: "pending" | "done" | "rejected" | "cancelled" | "failed" | "all" = "pending") => request<ApprovalList>(`/approvals?status=${status}`),
    approve: (id: string, note?: string) =>
      request<{ approval: Approval }>(`/approvals/${encodeURIComponent(id)}/approve`, { method: "POST", body: json({ note: note || null }) }),
    reject: (id: string, note: string) => request<Approval>(`/approvals/${encodeURIComponent(id)}/reject`, { method: "POST", body: json({ note }) }),
    cancel: (id: string) => request<Approval>(`/approvals/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
    rule: () => request<ApprovalRule>("/platform/approvals"),
    setRule: (body: { on: boolean; categories: string[]; reason: string }) =>
      request<{ on: boolean; categories: string[] }>("/platform/approvals", { method: "PUT", body: json(body) }),
  },
  audit: (params: { before?: string | null; action?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.before) q.set("before", params.before);
    if (params.action) q.set("action", params.action);
    q.set("limit", String(params.limit ?? 50));
    return request<StaffActionPage>(`/audit?${q}`);
  },
  runs: {
    list: (params: Record<string, string | undefined>) => request<RunList>(`/runs?${query(params)}`),
    running: () => request<RunList>("/runs/running"),
    stats: (hours: number) => request<RunStats>(`/runs/stats?hours=${hours}`),
    get: (id: string) => request<RunDetail>(`/runs/${encodeURIComponent(id)}`),
    stop: (id: string, reason: string) => request<{ stopping: boolean }>(`/runs/${encodeURIComponent(id)}/stop`, { method: "POST", body: json({ reason }) }),
  },
  objects: {
    list: (template: string) => request<ProfileWhere & { objects: ObjectSummary[] }>(`/objects/${encodeURIComponent(template)}`),
    get: (template: string, type: string, page = 0) =>
      request<ProfileWhere & ObjectProfile>(`/objects/${encodeURIComponent(template)}/${encodeURIComponent(type)}?page=${page}`),
    things: (template: string, type: string, q = "", page = 0) =>
      request<Paged<ThingSummary>>(`/objects/${encodeURIComponent(template)}/${encodeURIComponent(type)}/things?page=${page}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
    thing: (template: string, type: string, id: string, page = 0) =>
      request<ProfileWhere & ThingProfile>(`/objects/${encodeURIComponent(template)}/${encodeURIComponent(type)}/things/${encodeURIComponent(id)}?page=${page}`),
  },

  processes: {
    list: (params: Record<string, string | undefined>) => request<ProcessList>(`/processes?${query(params)}`),
    get: (id: string) => request<ProcessDetail>(`/processes/${encodeURIComponent(id)}`),
    retry: (id: string, reason: string) => request<{ status: string; event_id: string }>(`/processes/${encodeURIComponent(id)}/retry`, { method: "POST", body: json({ reason }) }),
    fileUrl: (id: string) => `${API}/processes/${encodeURIComponent(id)}/file`,
  },
  events: {
    resend: (id: string, reason: string) => request<{ resent: boolean }>(`/events/${encodeURIComponent(id)}/resend`, { method: "POST", body: json({ reason }) }),
  },
  queues: () => request<QueuesPage>("/queues"),
  names: (tenants: string[], workspaces: string[]) => request<Names>(`/names?${query({ tenants: tenants.join(","), workspaces: workspaces.join(",") })}`),
  lessons: {
    list: () => request<{ items: SystemLesson[] }>("/lessons"),
    add: (content: string) => request<SystemLesson>("/lessons", { method: "POST", body: json({ content }) }),
    set: (id: string, status: SystemLesson["status"]) => request<SystemLesson>(`/lessons/${encodeURIComponent(id)}`, { method: "PATCH", body: json({ status }) }),
  },

  console: {
    metrics: () => request<BusinessMetrics>("/metrics"),
    atRisk: () => request<AtRisk[]>("/organizations/at-risk"),
    organization: (id: string) => request<OrganizationProfile>(`/organizations/${encodeURIComponent(id)}/profile`),
    addNote: (id: string, body: string, pinned = false) =>
      request<OrgNote>(`/organizations/${encodeURIComponent(id)}/notes`, { method: "POST", body: json({ body, pinned }) }),
    pinNote: (id: string, noteId: string) => request<OrgNote>(`/organizations/${encodeURIComponent(id)}/notes/${encodeURIComponent(noteId)}/pin`, { method: "POST" }),
    removeNote: (id: string, noteId: string) => request<void>(`/organizations/${encodeURIComponent(id)}/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" }),
    pauseAgent: (id: string, paused: boolean, reason: string) =>
      request<{ agent_paused: boolean }>(`/organizations/${encodeURIComponent(id)}/agent`, { method: "POST", body: json({ paused, reason }) }),
    person: (id: string) => request<PersonProfile>(`/people/${encodeURIComponent(id)}/profile`),
    forgetBrowsers: (id: string, reason: string) =>
      request<{ browsers_forgotten: number }>(`/people/${encodeURIComponent(id)}/forget-browsers`, { method: "POST", body: json({ reason }) }),
    platform: () => request<PlatformControls>("/platform"),
    setMaintenance: (on: boolean, message: string, reason: string) =>
      request<PlatformControls["maintenance"]>("/platform/maintenance", { method: "PUT", body: json({ on, message, reason }) }),
    setSwitch: (name: SwitchName, on: boolean, reason: string) =>
      request<Record<SwitchName, boolean>>("/platform/switches", { method: "PUT", body: json({ name, on, reason }) }),
    announce: (body: { title: string; body: string; severity: Severity; tenant_id?: string | null; starts_at?: string | null; ends_at?: string | null; reason: string }) =>
      request<Announcement>("/announcements", { method: "POST", body: json(body) }),
    endAnnouncement: (id: string) => request<Announcement>(`/announcements/${encodeURIComponent(id)}/end`, { method: "POST" }),
    search: (q: string) => request<SearchAnswer>(`/search?q=${encodeURIComponent(q)}`),
    security: () => request<SecurityView>("/security"),
  },

  accounts: {
    organizations: (q?: string) => request<OrganizationAccount[]>(`/organizations${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    suspend: (id: string, reason: string) =>
      request<OrganizationAccount>(`/organizations/${encodeURIComponent(id)}/suspend`, { method: "POST", body: json({ reason }) }),
    reactivate: (id: string, reason: string) =>
      request<OrganizationAccount>(`/organizations/${encodeURIComponent(id)}/reactivate`, { method: "POST", body: json({ reason }) }),
    people: (q: string) => request<PersonAccount[]>(`/people?q=${encodeURIComponent(q)}`),
    disable: (id: string, reason: string) => request<PersonAccount>(`/people/${encodeURIComponent(id)}/disable`, { method: "POST", body: json({ reason }) }),
    enable: (id: string, reason: string) => request<PersonAccount>(`/people/${encodeURIComponent(id)}/enable`, { method: "POST", body: json({ reason }) }),
  },

  billing: {
    setup: () => request<BillingSetup>("/billing/limits"),
    catalog: (kind?: CatalogKind) => request<CatalogItem[]>(`/billing/catalog${kind ? `?kind=${kind}` : ""}`),
    create: (body: CatalogDraft) => request<CatalogItem>("/billing/catalog", { method: "POST", body: json(body) }),
    update: (id: string, body: CatalogDraft) => request<CatalogItem>(`/billing/catalog/${encodeURIComponent(id)}`, { method: "PATCH", body: json(body) }),
    newVersion: (id: string, body: CatalogDraft) =>
      request<CatalogItem>(`/billing/catalog/${encodeURIComponent(id)}/versions`, { method: "POST", body: json(body) }),
    publish: (id: string, reason: string) => request<CatalogItem>(`/billing/catalog/${encodeURIComponent(id)}/publish`, { method: "POST", body: json({ reason }) }),
    archive: (id: string, reason: string) => request<CatalogItem>(`/billing/catalog/${encodeURIComponent(id)}/archive`, { method: "POST", body: json({ reason }) }),
    move: (id: string, body: { to_id: string; at_period_end: boolean; reason: string }) =>
      request<{ moved: number }>(`/billing/catalog/${encodeURIComponent(id)}/move`, { method: "POST", body: json(body) }),
    organizations: (q?: string) => request<OrgBillingRow[]>(`/billing/organizations${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    organization: (id: string) => request<OrgBilling>(`/billing/organizations/${encodeURIComponent(id)}`),
    /** One of the changes staff make to an organization's billing: plan, addons, overrides, trial, credits. */
    change: (id: string, what: "plan" | "addons" | "overrides" | "trial" | "credits", body: Record<string, unknown>) =>
      request<{ done: string }>(`/billing/organizations/${encodeURIComponent(id)}/${what}`, { method: "POST", body: json(body) }),
    removeOverride: (id: string, overrideId: string, reason: string) =>
      request<{ done: string }>(`/billing/organizations/${encodeURIComponent(id)}/overrides/${encodeURIComponent(overrideId)}/remove`, { method: "POST", body: json({ reason }) }),
    followUps: (days = 7) => request<FollowUp[]>(`/billing/expiring?days=${days}`),
    summary: () => request<MoneySummary[]>("/billing/summary"),

    settings: () => request<BillingSettingsPage>("/billing/settings"),
    saveSettings: (body: Omit<SellerSettings, "updated_at" | "missing"> & { reason: string }) =>
      request<SellerSettings>("/billing/settings", { method: "PUT", body: json(body) }),
    addBankAccount: (body: Omit<BankAccountInfo, "id" | "active"> & { reason: string }) =>
      request<BankAccountInfo>("/billing/bank-accounts", { method: "POST", body: json(body) }),
    setBankAccountActive: (id: string, active: boolean, reason: string) =>
      request<BankAccountInfo>(`/billing/bank-accounts/${encodeURIComponent(id)}/active`, { method: "POST", body: json({ active, reason }) }),
    addTaxRate: (body: { country: string; name: string; rate_bp: number; reverse_charge_with_tax_id: boolean; reason: string }) =>
      request<TaxRateInfo>("/billing/tax-rates", { method: "POST", body: json(body) }),
    setTaxRateActive: (id: string, active: boolean, reason: string) =>
      request<TaxRateInfo>(`/billing/tax-rates/${encodeURIComponent(id)}/active`, { method: "POST", body: json({ active, reason }) }),

    invoices: (params: Record<string, string | undefined> = {}) => request<Invoice[]>(`/billing/invoices?${query(params)}`),
    invoice: (id: string) => request<Invoice>(`/billing/invoices/${encodeURIComponent(id)}`),
    createInvoice: (body: { tenant_id: string; lines: { item_id?: string; description?: string; unit_cents?: number; quantity: number }[];
      currency?: string; note?: string; issue: boolean; reason: string }) => request<Invoice>("/billing/invoices", { method: "POST", body: json(body) }),
    /** An action on one invoice that only needs a reason: issue, discard, void, switch-on. */
    invoiceAction: (id: string, action: "issue" | "discard" | "void" | "switch-on", reason: string) =>
      request<Invoice & { done?: string }>(`/billing/invoices/${encodeURIComponent(id)}/${action}`, { method: "POST", body: json({ reason }) }),
    recordPayment: (id: string, body: { method: string; amount_cents: number; received_on?: string; reference?: string; note?: string; reason: string }) =>
      request<BillingPayment & { invoice_status: string; done: string }>(`/billing/invoices/${encodeURIComponent(id)}/payments`, { method: "POST", body: json(body) }),
    creditNote: (id: string, body: { amount_cents: number; reason: string; refund_payment_id?: string; refund_reference?: string }) =>
      request<CreditNoteInfo>(`/billing/invoices/${encodeURIComponent(id)}/credit-notes`, { method: "POST", body: json(body) }),
    attachFiles: (paymentId: string, files: File[], reason: string) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      fd.append("reason", reason);
      return request<PaymentFileInfo[]>(`/billing/payments/${encodeURIComponent(paymentId)}/files`, { method: "POST", body: fd });
    },

    submissions: (status = "submitted") => request<Submission[]>(`/billing/submissions?status=${status}`),
    approve: (id: string, body: { amount_cents?: number; received_on?: string; overpaid_ok?: boolean; reason: string }) =>
      request<{ status: string; invoice_status: string; done: string }>(`/billing/submissions/${encodeURIComponent(id)}/approve`, { method: "POST", body: json(body) }),
    reject: (id: string, reason: string) =>
      request<{ status: string }>(`/billing/submissions/${encodeURIComponent(id)}/reject`, { method: "POST", body: json({ reason }) }),

    /** Files shown in <img>, <iframe> or opened in a tab (through this app's proxy, which signs them in). */
    invoicePdfUrl: (id: string, which: "pdf" | "receipt" = "pdf") => `${API}/billing/invoices/${encodeURIComponent(id)}/${which}`,
    creditNotePdfUrl: (id: string) => `${API}/billing/credit-notes/${encodeURIComponent(id)}/pdf`,
    submissionFileUrl: (id: string, fileId: string) => `${API}/billing/submissions/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}`,
    paymentFileUrl: (id: string, fileId: string) => `${API}/billing/payments/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}`,
    exportUrl: (what: "invoices" | "payments" | "credit_notes", from?: string, to?: string) =>
      `${API}/billing/export?${query({ what, date_from: from, date_to: to })}`,
  },
  /** The live view's address (an EventSource can't set headers; the proxy signs it in). */
  liveUrl: (params: Record<string, string | undefined>) => `${API}/live?${query(params)}`,
};

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  return q.toString();
}
