"use client";

import { Check, Copy, X } from "lucide-react";
import { useState } from "react";
import { Bar, Empty } from "@/components/ui/Blocks";
import { Button, IconButton } from "@/components/ui/Button";
import { Field, Textarea } from "@/components/ui/Field";
import { EmailChips } from "./EmailChips";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { ago, dateTime } from "@/lib/format";
import type { InvitationInfo, InviteIssued } from "@/lib/types";

const ADDRESS = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MAX_AT_ONCE = 20;

/** Addresses typed or pasted in any mix of commas, semicolons, spaces and lines; each once. */
export function parseAddresses(text: string): { valid: string[]; invalid: string[] } {
  const seen = new Set<string>();
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const address = raw.trim().replace(/^<|>$/g, "").toLowerCase();
    if (!address || seen.has(address)) continue;
    seen.add(address);
    (ADDRESS.test(address) ? valid : invalid).push(address);
  }
  return { valid, invalid };
}

export type RoleChoice = { value: string; label: string; help?: string };

type Outcome = { email: string; ok: boolean; message?: string; link?: string };

/** The link to hand over yourself, for when an email doesn't arrive. */
function LinkBox({ link, onHide, note }: { link: { email: string; url: string }; onHide: () => void; note?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex animate-rise flex-col gap-2 rounded-card border border-control bg-card p-4">
      <p className="text-body text-ink">
        {note ?? "The link for"} <span className="font-medium">{link.email}</span>. It works once, for that address.
      </p>
      <div className="flex min-w-0 items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink">{link.url}</code>
        <Button
          variant="secondary"
          onClick={async () => {
            await navigator.clipboard.writeText(link.url);
            setCopied(true);
          }}
        >
          {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
        </Button>
        <IconButton aria-label="Hide the link" onClick={onHide}><X /></IconButton>
      </div>
    </div>
  );
}

/**
 * Invite one or more people by email with a role, and an optional note that goes into the email. Each address is
 * invited on its own, so one that can't be (already a member, already invited) never stops the others.
 */
export function InviteForm({ roles, defaultRole, roleLabel, send, onSent, footer }: {
  roles: RoleChoice[];
  defaultRole: string;
  roleLabel: string;
  send: (email: string, role: string, note: string) => Promise<InviteIssued>;
  onSent: () => void;
  footer?: React.ReactNode;
}) {
  const [chips, setChips] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [role, setRole] = useState(defaultRole);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [shown, setShown] = useState<{ email: string; url: string } | null>(null);
  const { valid, invalid } = parseAddresses([...chips, draft].join(" "));
  const tooMany = valid.length > MAX_AT_ONCE;
  const chosen = roles.find((r) => r.value === role);

  const submit = async () => {
    setBusy(true);
    setShown(null);
    const results: Outcome[] = [];
    for (const address of valid) {
      try {
        const res = await send(address, role, note.trim());
        results.push({ email: address, ok: true, link: res.link });
      } catch (e) {
        results.push({ email: address, ok: false, message: e instanceof Error ? e.message : "Couldn't invite this address." });
      }
    }
    setOutcomes(results);
    setBusy(false);
    // What didn't go through stays in the box to fix; what did is cleared.
    const failed = new Set(results.filter((r) => !r.ok).map((r) => r.email));
    setChips([...failed, ...invalid]);
    setDraft("");
    onSent();
  };

  return (
    <div className="flex flex-col gap-4">
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid.length && !tooMany && !busy) void submit();
        }}
      >
        <Field
          label="Email addresses"
          help={
            invalid.length
              ? `Not an email address: ${invalid.join(", ")}`
              : tooMany
                ? `Up to ${MAX_AT_ONCE} at a time.`
                : "Press space after each address. Backspace brings the last one back to edit."
          }
        >
          {(id) => (
            <EmailChips
              id={id}
              chips={chips}
              draft={draft}
              onChange={(c, d) => {
                setChips(c);
                setDraft(d);
              }}
              isValid={(a) => ADDRESS.test(a)}
              placeholder="name@company.com, other@company.com"
              disabled={busy}
            />
          )}
        </Field>
        <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-[14rem_1fr]">
          <Field label={roleLabel} help={chosen?.help}>
            {(id) => <Select id={id} value={role} onChange={setRole} options={roles.map((r) => ({ value: r.value, label: r.label }))} />}
          </Field>
          <Field label="A note for the email (optional)" help="Shown in the invitation, for example what they will work on.">
            {(id) => <Textarea id={id} rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />}
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={!valid.length || tooMany || busy}>
            {busy ? "Sending…" : valid.length > 1 ? `Send ${valid.length} invitations` : "Send invitation"}
          </Button>
          {footer}
        </div>
      </form>

      {outcomes.length > 0 && (
        <ul className="flex animate-rise flex-col rounded-card border border-border bg-card">
          {outcomes.map((o) => (
            <li key={o.email} className="flex flex-wrap items-center gap-3 border-b border-rule px-4 py-2.5 last:border-0">
              <span className="min-w-0 flex-1 truncate text-body text-ink">{o.email}</span>
              {o.ok ? (
                <>
                  <span className="text-meta text-muted">Invited. The email is on its way.</span>
                  {o.link && <Button size="sm" variant="ghost" onClick={() => setShown({ email: o.email, url: o.link! })}>Show link</Button>}
                </>
              ) : (
                <span className="text-meta text-negative">{o.message}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {shown && <LinkBox link={shown} onHide={() => setShown(null)} />}
    </div>
  );
}

function EmailPill({ i }: { i: InvitationInfo }) {
  switch (i.email_status) {
    case "SENDING":
      return <Pill tone="info">Sending…</Pill>;
    case "SENT":
      return <Pill tone="positive">Emailed{i.email_sent_at ? ` ${ago(i.email_sent_at).toLowerCase()}` : ""}</Pill>;
    case "FAILED":
      return <Pill tone="negative">Couldn’t send</Pill>;
    default:
      return <Pill tone="neutral">Link only</Pill>;
  }
}

/** Invitations not accepted yet: whether the email went, send it again, get a fresh link, or withdraw it. */
export function PendingInvitations({ invitations, loading, resend, renew, revoke }: {
  invitations: InvitationInfo[] | undefined;
  loading: boolean;
  resend: (id: string) => Promise<InviteIssued>;
  renew: (id: string) => Promise<InviteIssued>;
  revoke: (id: string) => Promise<unknown>;
}) {
  const [working, setWorking] = useState<string | null>(null);
  const [problem, setProblem] = useState<{ id: string; message: string } | null>(null);
  const [shown, setShown] = useState<{ email: string; url: string } | null>(null);

  const run = async (id: string, action: () => Promise<unknown>, show?: (res: InviteIssued) => void) => {
    setWorking(id);
    setProblem(null);
    try {
      const res = await action();
      if (show) show(res as InviteIssued);
    } catch (e) {
      setProblem({ id, message: e instanceof Error ? e.message : "That didn't work." });
    } finally {
      setWorking(null);
    }
  };

  if (loading) return <Bar className="h-12 w-full" />;
  if (!invitations?.length) return <Empty title="No pending invitations" />;
  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col rounded-card border border-border bg-card">
        {invitations.map((i) => (
          <li key={i.id} className="flex flex-col gap-1 border-b border-rule px-4 py-3 last:border-0">
            <div className="flex flex-wrap items-center gap-3">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body font-medium text-ink">{i.email}</span>
                <span className="block text-meta text-muted">
                  {i.role_label}, invited by {i.invited_by}, {i.status === "EXPIRED" ? "expired" : `expires ${dateTime(i.expires_at)}`}
                </span>
              </span>
              {i.status === "EXPIRED" ? <Pill tone="warning">Expired</Pill> : <EmailPill i={i} />}
              <Button size="sm" variant="secondary" disabled={working === i.id} onClick={() => run(i.id, () => resend(i.id))}>
                {i.email_status === "FAILED" ? "Try again" : "Resend"}
              </Button>
              <Button size="sm" variant="ghost" disabled={working === i.id} onClick={() => run(i.id, () => renew(i.id), (res) => setShown({ email: res.invitation.email, url: res.link }))}>
                New link
              </Button>
              <Button size="sm" variant="ghost" disabled={working === i.id} onClick={() => run(i.id, () => revoke(i.id))}>Revoke</Button>
            </div>
            {i.email_status === "FAILED" && i.email_error && <p className="text-meta text-negative">{i.email_error} You can still copy a new link and send it yourself.</p>}
            {problem?.id === i.id && <p className="text-meta text-negative">{problem.message}</p>}
          </li>
        ))}
      </ul>
      {shown && <LinkBox link={shown} onHide={() => setShown(null)} note="A new link, for" />}
    </div>
  );
}
