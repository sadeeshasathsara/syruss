import { NextResponse } from "next/server";
import { authConfig } from "@/lib/auth/config";
import { needsRefresh, refresh } from "@/lib/auth/oidc";
import { clearSession, readSession, writeSession } from "@/lib/auth/session";

/**
 * How the signed-in person signs in, for the app's own security settings: whether they have a password and since when,
 * their authenticator apps, and whether Google is linked. Read from Keycloak's account API with their own token, so it
 * shows only what is theirs; the app holds no Keycloak admin access. Changes are single Keycloak steps
 * (/api/auth/login?action=...), never made here.
 */

type KcCredential = { id: string; type: string; userLabel?: string; createdDate?: number };
type KcCredentialGroup = { type: string; userCredentialMetadatas?: { credential: KcCredential }[] };
type KcLinkedAccount = { providerAlias: string; connected: boolean; linkedUsername?: string };

export type AccountSecurity = {
  password: { set: boolean; since: string | null };
  authenticators: { id: string; label: string; added: string | null }[];
  google: { linked: boolean; account: string | null } | null;
};

const when = (ms?: number) => (ms ? new Date(ms).toISOString() : null);

export async function GET() {
  let session = await readSession();
  if (!session) return NextResponse.json({ detail: { code: "signed_out", message: "Sign in to continue." } }, { status: 401 });
  if (needsRefresh(session)) {
    const fresh = await refresh(session);
    if (fresh === "unreachable") return NextResponse.json({ detail: "The sign-in service can't be reached right now." }, { status: 502 });
    if (!fresh) {
      await clearSession();
      return NextResponse.json({ detail: { code: "signed_out", message: "Your session ended. Sign in again." } }, { status: 401 });
    }
    session = fresh;
    await writeSession(fresh);
  }
  const account = `${authConfig().issuer}/account`;
  const headers = { Authorization: `Bearer ${session.accessToken}`, Accept: "application/json" };
  try {
    const [credentials, linked] = await Promise.all([
      fetch(`${account}/credentials`, { headers, cache: "no-store" }),
      fetch(`${account}/linked-accounts`, { headers, cache: "no-store" }),
    ]);
    if (!credentials.ok) return NextResponse.json({ detail: "Couldn't read your sign-in settings." }, { status: 502 });
    const groups = (await credentials.json()) as KcCredentialGroup[];
    const of = (type: string) => groups.find((g) => g.type === type)?.userCredentialMetadatas?.map((m) => m.credential) ?? [];
    const password = of("password")[0];
    const google = linked.ok ? ((await linked.json()) as KcLinkedAccount[]).find((a) => a.providerAlias === "google") : undefined;
    const body: AccountSecurity = {
      password: { set: !!password, since: when(password?.createdDate) },
      authenticators: of("otp").map((c) => ({ id: c.id, label: c.userLabel || "Authenticator app", added: when(c.createdDate) })),
      google: google ? { linked: google.connected, account: google.linkedUsername ?? null } : null,
    };
    return NextResponse.json(body, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ detail: "The sign-in service can't be reached right now." }, { status: 502 });
  }
}
