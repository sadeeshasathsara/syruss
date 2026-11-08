import type {
  BankAccountInfo, BillingPayment, BillingSettingsPage, BillingSetup, CatalogDraft, CatalogItem, CatalogKind, CreditNoteInfo, FollowUp,
  Invoice, OrgBilling, OrgBillingRow, PaymentFileInfo, SellerSettings, Submission, TaxRateInfo,
  Grant, GrantList, Names, PlatformHealth, PlatformStats, ProcessDetail, ProcessList, QueuesPage, RunDetail, RunList, RunStats,
  StaffActionPage, StaffList, StaffMe, StaffMember, StaffRole,
} from "./types";

/** Typed client for the platform tools (/api/v1/sysadmin), through this app's own proxy. */

const API = "/api/v1/sysadmin";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

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
    window.location.assign(`/api/auth/login?returnTo=${encodeURIComponent(here())}`);
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
      window.location.assign(`/api/auth/login?reauth=1&returnTo=${encodeURIComponent(here())}`);
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
    add: (body: { email: string; role: StaffRole; reason?: string }) => request<StaffMember>("/staff", { method: "POST", body: json(body) }),
    changeRole: (userId: string, body: { role: StaffRole; reason?: string }) =>
      request<StaffMember>(`/staff/${encodeURIComponent(userId)}`, { method: "PATCH", body: json(body) }),
    remove: (userId: string, reason?: string) =>
      request<void>(`/staff/${encodeURIComponent(userId)}${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`, { method: "DELETE" }),
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
  access: {
    list: () => request<GrantList>("/access"),
    open: (body: { tenant_id: string; reason: string; minutes: number }) => request<Grant>("/access", { method: "POST", body: json(body) }),
    end: (id: string) => request<void>(`/access/${encodeURIComponent(id)}`, { method: "DELETE" }),
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
