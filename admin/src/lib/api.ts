import type { PlatformHealth, PlatformStats, StaffActionPage, StaffList, StaffMe, StaffMember, StaffRole } from "./types";

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
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
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
};
