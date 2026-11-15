import { NextRequest, NextResponse } from "next/server";
import { authConfig } from "@/lib/auth/config";
import { readSession, seal, unseal } from "@/lib/auth/session";

/**
 * Keeps what someone was in the middle of while they sign in again (a form they had filled in), without trusting the browser with it.
 *
 *   save     the browser sends the draft; it comes back sealed with this app's own secret (AES-256-GCM) as an opaque blob, tied to the
 *            person who was signed in, the page, and the next 15 minutes. The browser can keep that blob but can't read or change it.
 *   restore  the browser sends the blob back once they are signed in again; it is opened only for the same person, on the same page,
 *            before it expires.
 *
 * Anything that looks like a secret (a password, key, token or code) is dropped here whatever the browser sent: those are never kept.
 */
const MAX_CHARS = 16_000;
const TTL_SECONDS = 900;
const SECRETISH = /pass(word|phrase)?|secret|token|credential|authorization|private|otp|(^|[_-])(api)?key($|[_-])|code/i;

function scrub(value: unknown, depth = 0): unknown {
  if (depth > 6) return undefined;
  if (Array.isArray(value)) return value.slice(0, 200).map((v) => scrub(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) if (!SECRETISH.test(k)) out[k] = scrub(v, depth + 1);
    return out;
  }
  return typeof value === "string" ? value.slice(0, 4000) : value;
}

const reply = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } });

export async function POST(request: NextRequest) {
  // Only this app's own pages may ask (a form posted from another site can't send JSON, and its Origin would differ).
  const origin = request.headers.get("origin");
  if (origin && origin !== authConfig().appOrigin) return reply({ error: "forbidden" }, 403);
  if (!request.headers.get("content-type")?.includes("application/json")) return reply({ error: "json" }, 415);
  let body: { op?: string; path?: unknown; data?: unknown; blob?: unknown };
  try {
    body = await request.json();
  } catch {
    return reply({ error: "json" }, 400);
  }
  const path = typeof body.path === "string" && body.path.startsWith("/") && body.path.length <= 300 ? body.path.split("?")[0] : null;
  if (!path) return reply({ error: "path" }, 400);
  const session = await readSession();

  if (body.op === "save") {
    if (!body.data || typeof body.data !== "object" || Array.isArray(body.data)) return reply({ error: "data" }, 400);
    const data = scrub(body.data);
    if (JSON.stringify(data).length > MAX_CHARS) return reply({ error: "too large" }, 413);
    return reply({ blob: await seal({ k: "draft", p: path, s: session?.sub ?? "", d: data as Record<string, unknown> }, TTL_SECONDS) });
  }
  if (body.op === "restore") {
    if (!session) return reply({ error: "signed out" }, 401);
    const opened = typeof body.blob === "string" ? await unseal<{ k?: string; p?: string; s?: string; d?: unknown }>(body.blob) : null;
    if (!opened || opened.k !== "draft" || opened.p !== path) return reply({ data: null });
    if (opened.s && opened.s !== session.sub) return reply({ data: null }); // someone else signed in: it was not theirs
    return reply({ data: opened.d ?? null });
  }
  return reply({ error: "op" }, 400);
}
