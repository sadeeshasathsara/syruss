import { NextRequest, NextResponse } from "next/server";
import { authConfig } from "@/lib/auth/config";
import { deviceToken } from "@/lib/auth/device";
import { needsRefresh, refresh } from "@/lib/auth/oidc";
import { clearSession, readSession, writeSession } from "@/lib/auth/session";

/**
 * The browser's only way to the core API. It carries the signed-in staff member's access token (which the browser never
 * sees) and passes on the platform tools (/api/v1/sysadmin/*) only: this app has no business with anything else.
 * The API decides what they may do; this only proves who is asking.
 */

const API = (process.env.API_ORIGIN ?? "http://localhost:8000").replace(/\/$/, "");
const FORWARD = ["content-type", "accept"];
// The last two keep a file someone uploaded (a payment slip) from being read as anything else, or running anything.
const PASS_BACK = ["content-type", "content-disposition", "cache-control", "retry-after", "x-content-type-options", "content-security-policy"];

function problem(status: number, detail: unknown) {
  return NextResponse.json({ detail }, { status });
}

async function proxy(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path[0] !== "sysadmin") return problem(404, "Not found.");
  const unsafe = !["GET", "HEAD"].includes(request.method);
  if (unsafe && request.headers.get("origin") !== authConfig().appOrigin) {
    return problem(403, { code: "bad_origin", message: "This request didn't come from the admin app." });
  }

  let session = await readSession();
  if (!session) return problem(401, { code: "signed_out", message: "Sign in to continue." });
  let renewed = false;
  if (needsRefresh(session)) {
    const fresh = await refresh(session);
    if (fresh === "unreachable") return problem(502, "The server can't be reached right now.");
    if (!fresh) {
      await clearSession();
      return problem(401, { code: "signed_out", message: "Your session ended. Sign in again." });
    }
    session = fresh;
    renewed = true;
  }

  const headers = new Headers({ Authorization: `Bearer ${session.accessToken}` });
  const device = await deviceToken();
  if (device) headers.set("X-Device-Token", device);
  for (const name of FORWARD) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  // For the platform audit log: the address the staff member's browser came from, as the edge saw it.
  const client = (request.headers.get("x-forwarded-for") ?? "").split(",")[0].trim();
  if (client) headers.set("X-Forwarded-For", client);

  const init: RequestInit & { duplex?: "half" } = { method: request.method, headers, redirect: "manual", cache: "no-store" };
  if (unsafe && request.body) {
    init.body = request.body;
    init.duplex = "half";
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/api/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, init);
  } catch {
    return problem(502, "The server can't be reached right now.");
  }

  const out = new Headers();
  for (const name of PASS_BACK) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  const response = new NextResponse(upstream.status === 204 || upstream.status === 304 ? null : upstream.body, { status: upstream.status, headers: out });
  if (renewed) await writeSession(session);
  return response;
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
