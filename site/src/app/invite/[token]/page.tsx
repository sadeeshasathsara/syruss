import type { Metadata } from "next";
import { AccountShell } from "@/components/AccountShell";
import { ApiError, api, type Context } from "@/lib/api";
import { readSession } from "@/lib/auth/session";
import { AcceptButton } from "./AcceptButton";

export const metadata: Metadata = { title: "Invitation" };
export const dynamic = "force-dynamic";

type Invitation = {
  kind: "workspace" | "organization";
  organization: { name: string };
  workspace?: { name: string };
  email: string;
  role_label: string;
  invited_by: string;
  status: "PENDING" | "ACCEPTED" | "REVOKED" | "EXPIRED";
};

async function lookup(token: string): Promise<Invitation | null> {
  // The previews are public (the unguessable link is the key), so no sign-in is needed to read them.
  const base = (process.env.API_ORIGIN ?? "http://localhost:8000").replace(/\/$/, "");
  for (const path of [`/invitations/${token}`, `/org-invitations/${token}`]) {
    const response = await fetch(`${base}/api/v1${path}`, { cache: "no-store" });
    if (response.ok) return (await response.json()) as Invitation;
  }
  return null;
}

const WHY: Record<string, string> = {
  ACCEPTED: "This invitation was already used.",
  REVOKED: "This invitation was withdrawn.",
  EXPIRED: "This invitation has expired.",
};

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const invitation = /^[A-Za-z0-9_-]{16,80}$/.test(token) ? await lookup(token) : null;
  const session = await readSession().catch(() => null);

  if (!invitation) {
    return (
      <AccountShell signedIn={!!session}>
        <div className="sheet">
          <h1>This link isn't valid</h1>
          <p className="lede">Ask the person who invited you for a new one.</p>
        </div>
      </AccountShell>
    );
  }

  let onboarded = false;
  if (session) {
    try {
      onboarded = (await api<Context>(session, "/me/context")).onboarded;
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
  }
  const returnTo = `/invite/${token}`;
  const what = invitation.kind === "workspace" ? invitation.workspace?.name : invitation.organization.name;
  const open = invitation.status === "PENDING";

  return (
    <AccountShell signedIn={!!session}>
      <div className="sheet">
        <h1>{open ? `Join ${what}` : "Invitation"}</h1>
        <p className="lede">
          {open
            ? `${invitation.invited_by} invited you${invitation.kind === "workspace" ? ` to a workspace in ${invitation.organization.name}` : " to their organization"}.`
            : WHY[invitation.status]}
        </p>
        {open ? (
          <dl className="invite-meta">
            <div><dt>Invited address</dt><dd>{invitation.email}</dd></div>
            <div><dt>Your role</dt><dd>{invitation.role_label}</dd></div>
          </dl>
        ) : null}
        {open && !session ? (
          <>
            <a className="button" href={`/api/auth/login?mode=signin&returnTo=${encodeURIComponent(returnTo)}`}>Sign in to accept</a>
            <a className="button button-quiet" href={`/api/auth/login?mode=signup&returnTo=${encodeURIComponent(returnTo)}`}>Create an account</a>
            <p className="lede" style={{ fontSize: 15 }}>Use the email address this invitation was sent to ({invitation.email}).</p>
          </>
        ) : null}
        {open && session && !onboarded ? (
          <a className="button" href={`/onboarding?invite=${encodeURIComponent(token)}`}>Set up your account to join</a>
        ) : null}
        {open && session && onboarded ? <AcceptButton token={token} kind={invitation.kind} /> : null}
      </div>
    </AccountShell>
  );
}
