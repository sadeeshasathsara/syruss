"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Info, KeyRound, ShieldCheck, Smartphone } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Section } from "@/components/settings/kit";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { loadPage } from "@/lib/browser";
import { cn, dateTime } from "@/lib/format";
import type { AccountSecurity } from "@/app/api/account/security/route";

/**
 * Password, authenticator apps and Google, managed here in Syruss. A change is one Syruss sign-in step (set a password,
 * scan a code, confirm a removal) that comes straight back to this page: the sign-in service's own account pages are
 * never shown.
 */

const STARTED = "syruss-account-step";
type Step = "CONFIGURE_TOTP" | "UPDATE_PASSWORD" | "REMOVE_TOTP";

const DONE: Record<Step, string> = {
  CONFIGURE_TOTP: "Two-step verification is on. Your authenticator app's code is asked when you sign in.",
  UPDATE_PASSWORD: "Your password is changed.",
  REMOVE_TOTP: "That authenticator app was removed.",
};

async function readSecurity(): Promise<AccountSecurity> {
  const res = await fetch("/api/account/security", { cache: "no-store" });
  if (!res.ok) throw new Error("Couldn't read your sign-in settings. Try again in a moment.");
  return res.json();
}

/** Leaves for the one sign-in step, remembering which, and comes back to this page. */
function start(step: Step, action: string, here: string) {
  try {
    sessionStorage.setItem(STARTED, step);
  } catch {
    /* only the message after coming back is lost */
  }
  loadPage(`/api/auth/login?action=${encodeURIComponent(action)}&returnTo=${encodeURIComponent(here)}`);
}

export function SignInMethods() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const security = useQuery({ queryKey: ["account-security"], queryFn: readSecurity, retry: 1 });
  const [notice, setNotice] = useState<{ tone: "done" | "info"; text: string } | null>(null);

  // Back from a step: say how it went, show the new state, and drop ?step= from the address
  useEffect(() => {
    const result = params.get("step");
    if (!result) return;
    let step: Step | null = null;
    try {
      step = sessionStorage.getItem(STARTED) as Step | null;
      sessionStorage.removeItem(STARTED);
    } catch {
      step = null;
    }
    const text = result === "done" ? (step ? DONE[step] : "Saved.")
      : result === "cancelled" ? "Nothing was changed." : "That didn't go through. Try again.";
    queueMicrotask(() => setNotice({ tone: result === "done" ? "done" : "info", text }));
    queryClient.invalidateQueries({ queryKey: ["account-security"] });
    router.replace(pathname, { scroll: false });
  }, [params, pathname, router, queryClient]);

  const s = security.data;
  const twoStep = !!s?.authenticators.length;
  return (
    <>
      {notice && (
        <p role="status" className={cn("mb-2 flex items-start gap-2 rounded-card px-4 py-3 text-body",
          notice.tone === "done" ? "bg-[var(--pill-positive-bg)] text-[var(--pill-positive-ink)]" : "bg-raised text-muted")}>
          {notice.tone === "done" ? <CheckCircle2 className="mt-0.5 size-4 flex-none" /> : <Info className="mt-0.5 size-4 flex-none" />}
          {notice.text}
        </p>
      )}

      <Section title="Password" description="You'll be asked for your current password first if you haven't signed in recently.">
        {!s ? (security.isError ? <p className="text-body text-negative">{(security.error as Error).message}</p> : <Bar className="h-9 w-full" />) : (
          <div className="flex flex-wrap items-center gap-3">
            <KeyRound className="size-4 flex-none text-muted" aria-hidden />
            <p className="min-w-0 flex-1 text-body text-ink">
              {s.password.set
                ? <>Set{s.password.since && <span className="text-muted"> · last changed {dateTime(s.password.since)}</span>}</>
                : <span className="text-muted">No password: you sign in with Google. Set one to sign in with your email too.</span>}
            </p>
            <Button variant="secondary" onClick={() => start("UPDATE_PASSWORD", "UPDATE_PASSWORD", pathname)}>
              {s.password.set ? "Change password" : "Set a password"}
            </Button>
          </div>
        )}
      </Section>

      <Section title="Two-step verification"
        description="A code from an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password…) as well as your password, so a stolen password isn't enough. Tick “Keep me signed in” when you sign in and it's asked once a month on that device.">
        {!s ? <Bar className="h-9 w-full" /> : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <ShieldCheck className={cn("size-4 flex-none", twoStep ? "text-positive" : "text-muted")} aria-hidden />
              <p className="min-w-0 flex-1 text-body text-ink">
                {twoStep ? "On" : "Off"} {!twoStep && <span className="text-muted">· only your password protects this account</span>}
              </p>
              <Pill tone={twoStep ? "positive" : "warning"}>{twoStep ? "Protected" : "Recommended"}</Pill>
            </div>
            {twoStep && (
              <ul className="flex flex-col divide-y divide-rule rounded-card border border-border">
                {s.authenticators.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                    <Smartphone className="size-4 flex-none text-muted" aria-hidden />
                    <span className="min-w-0 flex-1 text-body text-ink">
                      {a.label}{a.added && <span className="text-muted"> · added {dateTime(a.added)}</span>}
                    </span>
                    <Button size="sm" variant="secondary" onClick={() => start("REMOVE_TOTP", `delete_credential:${a.id}`, pathname)}>Remove</Button>
                  </li>
                ))}
              </ul>
            )}
            <div>
              <Button variant={twoStep ? "secondary" : "primary"} onClick={() => start("CONFIGURE_TOTP", "CONFIGURE_TOTP", pathname)}>
                {twoStep ? "Add another authenticator app" : "Set up two-step verification"}
              </Button>
            </div>
          </div>
        )}
      </Section>

      {s?.google && (
        <Section title="Google" description="Sign in with your Google account instead of a password.">
          <p className="text-body text-ink">
            {s.google.linked ? <>Linked{s.google.account && <span className="text-muted"> · {s.google.account}</span>}</> : <span className="text-muted">Not linked</span>}
          </p>
        </Section>
      )}
    </>
  );
}
