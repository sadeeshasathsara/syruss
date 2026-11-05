import { EncryptJWT, jwtDecrypt, decodeJwt } from "jose";
import { cookies } from "next/headers";
import { authConfig } from "./config";

export type Session = {
  sub: string;
  accessToken: string;
  refreshToken: string;
  /** Lets Keycloak sign this person out without asking again. */
  idToken?: string;
  /** Access token expiry, epoch seconds. */
  accessExp: number;
  name?: string;
  email?: string;
};

type CookieJar = Awaited<ReturnType<typeof cookies>>;

const CHUNK = 3600; // a browser keeps ~4096 bytes per cookie, attributes included
const MAX_CHUNKS = 4;

async function key(): Promise<Uint8Array> {
  const data = new TextEncoder().encode(authConfig().sessionSecret);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", data));
}

export async function seal(payload: Record<string, unknown>, maxAgeSeconds: number): Promise<string> {
  return new EncryptJWT(payload)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(`${maxAgeSeconds}s`)
    .encrypt(await key());
}

export async function unseal<T = Record<string, unknown>>(token: string): Promise<T | null> {
  try {
    const { payload } = await jwtDecrypt(token, await key(), { clockTolerance: 5 });
    return payload as T;
  } catch {
    return null;
  }
}

function options(maxAge: number) {
  const cfg = authConfig();
  return { httpOnly: true, secure: cfg.secureCookies, sameSite: "lax" as const, path: "/", maxAge };
}

function chunkNames(name: string, jar: CookieJar): string[] {
  return jar.getAll().map((c) => c.name).filter((n) => n === name || n.startsWith(`${name}.`));
}

/** Writes a sealed value, split into chunks when it is long. Only callable where cookies may be set. */
export async function writeSealed(jar: CookieJar, name: string, payload: Record<string, unknown>, maxAge: number) {
  const token = await seal(payload, maxAge);
  const parts = token.match(new RegExp(`.{1,${CHUNK}}`, "g")) ?? [];
  if (parts.length > MAX_CHUNKS) throw new Error("Session is too large to store in cookies.");
  for (const stale of chunkNames(name, jar)) jar.delete(stale);
  if (parts.length === 1) jar.set(name, parts[0], options(maxAge));
  else parts.forEach((part, i) => jar.set(`${name}.${i}`, part, options(maxAge)));
}

export async function readSealed<T = Record<string, unknown>>(jar: CookieJar, name: string): Promise<T | null> {
  const whole = jar.get(name)?.value;
  if (whole) return unseal<T>(whole);
  const parts: string[] = [];
  for (let i = 0; i < MAX_CHUNKS; i++) {
    const part = jar.get(`${name}.${i}`)?.value;
    if (!part) break;
    parts.push(part);
  }
  return parts.length ? unseal<T>(parts.join("")) : null;
}

export function clearSealed(jar: CookieJar, name: string) {
  for (const n of chunkNames(name, jar)) jar.delete(n);
}

export async function readSession(): Promise<Session | null> {
  return readSealed<Session>(await cookies(), authConfig().cookieName);
}

export async function writeSession(session: Session, maxAgeSeconds = 36000) {
  await writeSealed(await cookies(), authConfig().cookieName, session, maxAgeSeconds);
}

export async function clearSession() {
  clearSealed(await cookies(), authConfig().cookieName);
}

/** Display details from the access token (the API verifies it on every call; this only greets someone). */
export function identityOf(accessToken: string): { sub: string; name?: string; email?: string } {
  const claims = decodeJwt(accessToken);
  return { sub: String(claims.sub), name: claims.name as string | undefined, email: claims.email as string | undefined };
}
