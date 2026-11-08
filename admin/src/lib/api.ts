import type {
  BillingRequest, BillingSetup, CatalogDraft, CatalogItem, CatalogKind, FollowUp, OrgBilling, OrgBillingRow,
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
      headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
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
    /** One of the changes staff make to an organization's billing: plan, addons, overrides, trial, credits, payments. */
    change: (id: string, what: "plan" | "addons" | "overrides" | "trial" | "credits" | "payments", body: Record<string, unknown>) =>
      request<{ done: string }>(`/billing/organizations/${encodeURIComponent(id)}/${what}`, { method: "POST", body: json(body) }),
    removeOverride: (id: string, overrideId: string, reason: string) =>
      request<{ done: string }>(`/billing/organizations/${encodeURIComponent(id)}/overrides/${encodeURIComponent(overrideId)}/remove`, { method: "POST", body: json({ reason }) }),
    requests: (status = "open") => request<BillingRequest[]>(`/billing/requests?status=${status}`),
    handle: (id: string, body: { action: "fulfil" | "decline"; reason: string; note?: string; amount_cents?: number; reference?: string }) =>
      request<{ done: string; status: string }>(`/billing/requests/${encodeURIComponent(id)}`, { method: "POST", body: json(body) }),
    followUps: (days = 7) => request<FollowUp[]>(`/billing/expiring?days=${days}`),
  },
  /** The live view's address (an EventSource can't set headers; the proxy signs it in). */
  liveUrl: (params: Record<string, string | undefined>) => `${API}/live?${query(params)}`,
};

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  return q.toString();
}
