"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { BrandMark } from "@/components/shell/Brand";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/format";
import type { JoinView } from "@/lib/types";

const field = "h-9 rounded-control border border-control bg-sheet px-2.5 text-body text-ink outline-none focus-visible:border-accent";

// Signing out ends this app's session and Keycloak's, so the right address can sign in (a real form post, as elsewhere).
function signOut() {
  const form = document.createElement("form");
  form.method = "post";
  form.action = "/api/auth/logout";
  document.body.appendChild(form);
  form.submit();
}

/**
 * Accepting an invitation to the platform staff. Signed in with the invited address (and that address confirmed with an
 * emailed code). When the address already has a Syruss customer account, everything accepting would delete is listed,
 * and nothing happens until the person ticks that they understand and confirms.
 */
export default function JoinPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const view = useQuery({ queryKey: ["join", token], queryFn: () => api.staff.viewJoin(token), retry: false });
  return (
    <div className="flex min-h-dvh items-start justify-center bg-frame px-4 py-12">
      <div className="flex w-full max-w-lg flex-col gap-5 rounded-sheet border border-border bg-sheet px-6 py-7">
        <div className="flex items-center gap-2"><BrandMark size={24} /><span className="text-section font-semibold text-ink">Syruss platform staff</span></div>
        {view.isError ? <p className="text-body text-negative">{view.error.message}</p>
          : !view.data ? <div className="flex flex-col gap-3"><Bar className="h-6 w-64" /><Bar className="h-24 w-full" /></div>
          : <Join token={token} v={view.data} onChange={() => view.refetch()} />}
      </div>
    </div>
  );
}

function Join({ token, v, onChange }: { token: string; v: JoinView; onChange: () => void }) {
  const router = useRouter();
  const [understood, setUnderstood] = useState(false);
  const accept = useMutation({ mutationFn: () => api.staff.acceptJoin(token, !!v.customer_account), onSuccess: () => router.replace("/overview") });
  const i = v.invitation;
  const title = i.role.charAt(0).toUpperCase() + i.role.slice(1);

  if (v.already_staff) {
    return (
      <>
        <p className="text-body text-ink">You&apos;re already on the platform staff.</p>
        <div><Button variant="primary" onClick={() => router.replace("/overview")}>Open the admin app</Button></div>
      </>
    );
  }
  if (v.problem && v.problem.code !== "email_unverified") {
    return (
      <>
        <p className="text-body text-ink">{v.problem.message}</p>
        {v.problem.code === "wrong_address" && <div><Button onClick={signOut}>Sign out</Button></div>}
      </>
    );
  }
  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="text-section font-semibold text-ink">Join as {title}</h1>
        <p className="text-body text-muted">{i.invited_by_name} invited {i.email}. {v.role_description}</p>
      </div>

      {v.problem?.code === "email_unverified" ? <ConfirmEmail token={token} email={i.email} onDone={onChange} /> : (
        <>
          {v.customer_account && <CustomerWarning account={v.customer_account} understood={understood} onUnderstood={setUnderstood} />}
          {accept.error && <p className="text-body text-negative">{accept.error.message}</p>}
          <div className="flex flex-wrap gap-2">
            <Button variant={v.customer_account ? "danger" : "primary"} disabled={accept.isPending || (!!v.customer_account && !understood)} onClick={() => accept.mutate()}>
              {accept.isPending ? "Joining…" : v.customer_account ? "Delete my customer account and join the staff" : "Join the platform staff"}
            </Button>
            <Button onClick={signOut}>Not now</Button>
          </div>
        </>
      )}
    </>
  );
}

/** Everything accepting removes, spelled out, with a tick to confirm it was read. */
function CustomerWarning({ account, understood, onUnderstood }: {
  account: NonNullable<JoinView["customer_account"]>; understood: boolean; onUnderstood: (v: boolean) => void;
}) {
  const closed = account.organizations.filter((o) => o.outcome === "closed");
  const left = account.organizations.filter((o) => o.outcome === "left");
  return (
    <div role="alert" className="flex flex-col gap-3 rounded-card bg-[var(--pill-negative-bg)] px-4 py-3 text-body text-[var(--pill-negative-ink)]">
      <p className="flex items-start gap-2 font-medium"><TriangleAlert className="mt-0.5 size-4 flex-none" aria-hidden />This address has a Syruss customer account. A staff account can only use the admin app, so joining deletes it:</p>
      <ul className="ml-6 list-disc space-y-1">
        {closed.map((o) => <li key={o.id}><span className="font-medium">{o.name}</span> will be closed: you are its only owner. Nobody can open it any more, and its documents and data are removed for good after 30 days.</li>)}
        {left.map((o) => <li key={o.id}>You leave <span className="font-medium">{o.name}</span> (you were {o.role}). The organization stays for its other members.</li>)}
        {account.workspaces > 0 && <li>You leave all {account.workspaces} workspace{account.workspaces === 1 ? "" : "s"} you are in.</li>}
        <li>Your customer profile, notifications and confirmed browsers are deleted. Only the admin app will open for this address.</li>
      </ul>
      <p>This can&apos;t be undone. To keep the customer account, ask for the invitation to be sent to a different address.</p>
      <label className="flex items-center gap-2 font-medium">
        <input type="checkbox" checked={understood} onChange={(e) => onUnderstood(e.target.checked)} className="size-4" />
        I understand that my customer account and these memberships will be deleted
      </label>
    </div>
  );
}

/** The emailed code that proves the address is theirs. */
function ConfirmEmail({ token, email, onDone }: { token: string; email: string; onDone: () => void }) {
  const [code, setCode] = useState("");
  const send = useMutation({ mutationFn: () => api.staff.sendJoinCode(token) });
  const confirm = useMutation({ mutationFn: () => api.staff.confirmJoinCode(token, code.replace(/\D/g, "")), onSuccess: onDone });
  const error = confirm.error ?? send.error;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-body text-ink">First, confirm that {email} is yours: we&apos;ll email it a 6-digit code.</p>
      {!send.isSuccess ? (
        <div><Button variant="primary" onClick={() => send.mutate()} disabled={send.isPending}>{send.isPending ? "Sending…" : "Email me a code"}</Button></div>
      ) : (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (code.replace(/\D/g, "").length >= 6) confirm.mutate(); }}>
          <label className="flex flex-col gap-1 text-meta text-muted">The code
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" className={cn(field, "w-40 tracking-widest")} autoFocus />
          </label>
          <Button type="submit" variant="primary" disabled={confirm.isPending}>Confirm</Button>
          <Button onClick={() => send.mutate()} disabled={send.isPending}>Send another</Button>
        </form>
      )}
      {error && <p className="text-body text-negative">{error instanceof ApiError ? error.message : String(error)}</p>}
    </div>
  );
}
