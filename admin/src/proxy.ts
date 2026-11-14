import { NextResponse, type NextRequest } from "next/server";

/**
 * A first, cheap check before any page renders: no session cookie, straight to sign-in. Anyone can forge a cookie name,
 * so this only saves a trip; the real checks are the API's, on every call (the admin app's token, then platform staff).
 */
const COOKIE = process.env.SESSION_COOKIE_NAME ?? "syruss_admin_session";

/**
 * The content security policy: only our own scripts run (each page's carry a one-time nonce), nothing is framed, forms
 * only go to us and the sign-in server. It is for production builds: the development server needs looser rules.
 */
function policy(nonce: string): string {
  const keycloak = new URL(process.env.OIDC_ISSUER ?? "http://localhost:8080").origin;
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    `form-action 'self' ${keycloak}`,
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
  // Staff sign in here, through this app's own client (password and a one-time code), not through the site.
  const login = new URL("/api/auth/login", request.url);
  login.searchParams.set("returnTo", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  // Pages only: the API proxy answers 401 itself, the auth routes must stay open, assets need no session.
  matcher: ["/((?!api/|_next/|signed-out|favicon.ico|icon.svg|.*\\.(?:svg|png|jpg|jpeg|webp|ico|css|js|map|txt)$).*)"],
};
