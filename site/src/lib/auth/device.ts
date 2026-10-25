import { randomBytes } from "node:crypto";
import { cookies } from "next/headers";
import { authConfig } from "./config";
import type { Session } from "./session";

// Each browser holds one long random token; the API stores only its hash. It is how the API tells a browser the person
// has already confirmed from one it hasn't. Shared by site/ and www/: with DEVICE_COOKIE_DOMAIN set to the parent domain,
// both apps read the same cookie, so a browser is confirmed once, not once per app.

const NAME = () => process.env.DEVICE_COOKIE_NAME ?? "syruss_device";
const YEAR = 60 * 60 * 24 * 400;
const API = () => (process.env.API_ORIGIN ?? "http://localhost:8000").replace(/\/$/, "");

export async function deviceToken(): Promise<string | undefined> {
  return (await cookies()).get(NAME())?.value;
}

/** This browser's token, made and remembered the first time. Only where cookies may be set (a route handler or action). */
export async function ensureDeviceToken(): Promise<string> {
  const jar = await cookies();
  const existing = jar.get(NAME())?.value;
  if (existing && /^[A-Za-z0-9_-]{32,100}$/.test(existing)) return existing;
  const token = randomBytes(32).toString("base64url");
  jar.set(NAME(), token, {
    httpOnly: true, secure: authConfig().secureCookies, sameSite: "lax", path: "/", maxAge: YEAR,
    ...(process.env.DEVICE_COOKIE_DOMAIN ? { domain: process.env.DEVICE_COOKIE_DOMAIN } : {}),
  });
  return token;
}

/** Right after sign-in: does the API know this browser? A new one is emailed a code and has to be confirmed. */
export async function checkDevice(session: Session, token: string, userAgent: string, ip: string): Promise<"trusted" | "challenge" | "unknown"> {
  try {
    const response = await fetch(`${API()}/api/v1/me/device/check`, {
      method: "POST",
      headers: { Authorization: `Bearer ${session.accessToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ token, user_agent: userAgent.slice(0, 400), ip: ip.slice(0, 64) }),
      cache: "no-store",
    });
    if (!response.ok) return "unknown";
    return (await response.json()).status === "challenge" ? "challenge" : "trusted";
  } catch {
    return "unknown"; // the API is unreachable: its own checks still apply to every call
  }
}
