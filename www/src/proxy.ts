import { NextResponse, type NextRequest } from "next/server";

/**
 * A first, cheap check before any page renders: no session cookie, no app. Anyone can forge a cookie name,
 * so this only saves a trip; the real checks are the API's, on every call (organization, then workspace).
 */
const COOKIE = process.env.SESSION_COOKIE_NAME ?? "syruss_session";
const SITE = (process.env.SITE_URL ?? "http://localhost:3100").replace(/\/$/, "");

/**
 * The content security policy: only our own scripts run (each page's carry a one-time nonce), nothing is framed, forms
 * only go to us, the site and the sign-in server. It is for production builds: the development server needs looser rules.
 */
function policy(nonce: string): string {
  const origin = (value: string | undefined, fallback: string) => new URL(value ?? fallback).origin;
  const forms = ["'self'", origin(process.env.SITE_URL, "http://localhost:3100"), origin(process.env.OIDC_ISSUER, "http://localhost:8080")];
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    `form-action ${forms.join(" ")}`,
    "frame-ancestors 'none'",
  ].join("; ");
}

function pass(request: NextRequest) {
  if (process.env.NODE_ENV !== "production") return NextResponse.next();
  const nonce = btoa(crypto.randomUUID());
  const csp = policy(nonce);
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export function proxy(request: NextRequest) {
  const has = request.cookies.has(COOKIE) || request.cookies.has(`${COOKIE}.0`);
  if (has) return pass(request);
  // Signing in happens on the site; it sends them straight back to the page they asked for.
  const wanted = request.nextUrl.pathname + request.nextUrl.search;
  const login = new URL("/api/auth/login", SITE);
  login.searchParams.set("mode", "signin");
  login.searchParams.set("returnTo", `/continue?next=${encodeURIComponent(wanted)}`);
  return NextResponse.redirect(login);
}

export const config = {
  // Pages only: the API proxy answers 401 itself, the auth routes must stay open, assets need no session.
  matcher: ["/((?!api/|_next/|signed-out|favicon.ico|icon.svg|.*\\.(?:svg|png|jpg|jpeg|webp|ico|css|js|map|txt)$).*)"],
};
