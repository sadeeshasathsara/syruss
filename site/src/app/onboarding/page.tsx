import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AccountShell } from "@/components/AccountShell";
import { api, appLogin, requireSession, type Context } from "@/lib/api";
import { emailStatus } from "./actions";
import { OrganizationForm, ProfileForm, VerifyEmailForm } from "./forms";

export const metadata: Metadata = { title: "Set up your profile" };
export const dynamic = "force-dynamic";

/** The steps in order; the email step is skipped for people whose address the sign-in already confirmed (Google). */
function Steps({ at, invite }: { at: "email" | "profile" | "organization"; invite: boolean }) {
  const all = [["email", "Your email"], ["profile", "Your profile"], ...(invite ? [] : [["organization", "Your organization"]])];
  return (
    <p className="steps">
      {all.map(([key, label], i) => (key === at ? <b key={key}>{i ? "· " : ""}{label}</b> : <span key={key}>{i ? "· " : ""}{label}</span>))}
    </p>
  );
}

/**
 * After the Keycloak account exists (email and password, or Google), short steps happen here: confirm the email
 * (not needed for Google), the profile, then the organization. Then the app.
 */
export default async function Onboarding({ searchParams }: { searchParams: Promise<{ invite?: string }> }) {
  const { invite } = await searchParams;
  const here = invite ? `/onboarding?invite=${invite}` : "/onboarding";
  const session = await requireSession(here);
  const ctx = await api<Context>(session, "/me/context", { returnTo: here });

  // Step one: prove the address is theirs with a code emailed to it.
  const email = ctx.identity.email ?? session.email ?? "";
  if (!ctx.onboarded && !ctx.identity.email_verified) {
    return (
      <AccountShell>
        <div className="sheet">
          <Steps at="email" invite={!!invite} />
          <h1>Confirm your email</h1>
          <p className="lede">We sent a 6-digit code to <b>{email}</b>. Enter it to create your account.</p>
          <VerifyEmailForm email={email} invite={invite} start={await emailStatus()} />
        </div>
      </AccountShell>
    );
  }

  // Step two: the profile that goes with the account.
  if (!ctx.onboarded) {
    return (
      <AccountShell>
        <div className="sheet">
          <Steps at="profile" invite={!!invite} />
          <h1>Tell us who you are</h1>
          <p className="lede">Your account is ready. This profile is how your teammates will see you.</p>
          <ProfileForm
            email={ctx.identity.email ?? session.email ?? ""}
            invite={invite}
            photo={ctx.identity.picture}
            defaults={{ first: ctx.identity.given_name ?? "", last: ctx.identity.family_name ?? "" }}
          />
        </div>
      </AccountShell>
    );
  }

  const org = ctx.organizations[0];
  // Already settled in: straight to the app.
  if (org) redirect(appLogin(`/${org.slug}`));

  // Joining by invitation instead: the invitation page does the rest.
  if (invite) redirect(`/invite/${invite}`);

  // Step two: the organization, named for them to start with.
  const first = ctx.user?.first_name ?? "";
  return (
    <AccountShell>
      <div className="sheet">
        <Steps at="organization" invite={false} />
        <h1>Name your organization</h1>
        <p className="lede">Your workspaces, documents and teammates live in an organization. You can change the name any time.</p>
        <OrganizationForm suggestedName={first ? `${first}'s organization` : "My organization"} />
      </div>
    </AccountShell>
  );
}
