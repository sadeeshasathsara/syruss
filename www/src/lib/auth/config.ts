// Shared by site/, www/ and admin/ (src/lib/auth is kept identical in each; `npm run check:auth-sync` in site/ verifies it).
// Everything here is read on the server only.

export type AuthConfig = {
  issuer: string;
  clientId: string;
  clientSecret: string;
  /** Public origin of THIS app (where Keycloak sends people back). */
  appOrigin: string;
  /** Public origin of the site (landing, sign-out page). */
  siteOrigin: string;
  sessionSecret: string;
  cookieName: string;
  secureCookies: boolean;
};

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set (see .env.example).`);
  return value;
}

let cached: AuthConfig | null = null;

export function authConfig(): AuthConfig {
  if (cached) return cached;
  const appOrigin = required("APP_ORIGIN").replace(/\/$/, "");
  const sessionSecret = required("SESSION_SECRET");
  if (sessionSecret.length < 32) throw new Error("SESSION_SECRET must be at least 32 characters.");
  cached = {
    issuer: required("OIDC_ISSUER").replace(/\/$/, ""),
    clientId: required("OIDC_CLIENT_ID"),
    clientSecret: required("OIDC_CLIENT_SECRET"),
    appOrigin,
    siteOrigin: (process.env.SITE_ORIGIN ?? appOrigin).replace(/\/$/, ""),
    sessionSecret,
    // Cookies are shared between ports on one host, so each app names its own.
    cookieName: process.env.SESSION_COOKIE_NAME ?? "syruss_session",
    secureCookies: appOrigin.startsWith("https://"),
  };
  return cached;
}

/**
 * Only same-site relative paths may be returned to after sign-in (no open redirects). Browsers drop tabs and line
 * breaks from an address before reading it, so "/\t/evil.example" would become "//evil.example": control characters
 * are refused, and the path must still point at this site once it is resolved.
 */
export function safeReturnTo(value: string | null | undefined, fallback = "/"): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u001f\u007f]/.test(value)) return fallback;
  try {
    const base = "https://return-to.invalid";
    if (new URL(value, base).origin !== base) return fallback;
  } catch {
    return fallback;
  }
  return value;
}
