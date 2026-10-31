import { redirect } from "next/navigation";
import { deviceToken } from "@/lib/auth/device";
import { needsRefresh } from "@/lib/auth/oidc";
import { readSession, type Session } from "@/lib/auth/session";

// Server-side calls to the core API as the signed-in person. The access token never reaches the browser.

const API = (process.env.API_ORIGIN ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

/** FastAPI errors come as a string, {code, message}, or a list of validation problems. */
function describe(detail: unknown): { message: string; code?: string } {
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) {
    const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
    const field = first?.loc?.filter((p) => typeof p === "string" && p !== "body").join(" ");
    return { message: first?.msg ? `${field ? `${field}: ` : ""}${first.msg}` : "Please check what you entered." };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; code?: string };
    return { message: d.message ?? "Something went wrong.", code: d.code };
  }
  return { message: "Something went wrong." };
}

/** The signed-in person, or off to sign in (and back). A nearly expired token is renewed first. */
export async function requireSession(returnTo: string): Promise<Session> {
  const session = await readSession();
  const to = encodeURIComponent(returnTo);
  if (!session) redirect(`/api/auth/login?returnTo=${to}`);
  if (needsRefresh(session)) redirect(`/api/auth/refresh?returnTo=${to}`);
  return session;
}

type Options = { method?: string; body?: unknown; org?: string; returnTo?: string };

export async function api<T>(session: Session, path: string, opts: Options = {}): Promise<T> {
  const device = await deviceToken();
  const response = await fetch(`${API}/api/v1${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers: {
      Authorization: `Bearer ${session.accessToken}`,
      ...(device ? { "X-Device-Token": device } : {}),
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(opts.org ? { "X-Org-Slug": opts.org } : {}),
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  if (response.status === 401 && opts.returnTo) redirect(`/api/auth/login?returnTo=${encodeURIComponent(opts.returnTo)}`);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const { message, code } = describe((payload as { detail?: unknown }).detail);
    // A browser the account hasn't confirmed: the code page, then back to what was asked for.
    if (response.status === 403 && code === "device_unverified") redirect(`/verify-device?next=${encodeURIComponent(opts.returnTo ?? "/continue")}`);
    throw new ApiError(response.status, message, code);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export type Context = {
  onboarded: boolean;
  identity: { email: string | null; email_verified: boolean; given_name: string | null; family_name: string | null; picture: string | null };
  user: { id: string; email: string; first_name: string; last_name: string; name: string; phone: string | null; timezone: string; avatar_url: string | null } | null;
  organizations: { id: string; slug: string; name: string; role: string; role_label: string; permissions: string[] }[];
  workspaces: { id: string; slug: string; name: string; org_slug: string; role: string; role_label: string; permissions: string[] }[];
};

export const appUrl = (path = "/") => `${(process.env.WORKSPACE_APP_URL ?? "http://localhost:3000").replace(/\/$/, "")}${path}`;

/** Into the Syruss app at `path`. The app signs itself in with the Keycloak session that already exists, silently. */
export const appLogin = (path: string) => appUrl(`/api/auth/login?returnTo=${encodeURIComponent(path)}`);
