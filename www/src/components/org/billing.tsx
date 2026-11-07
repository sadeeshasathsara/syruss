"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NoAccess, Section, SettingsHeader } from "@/components/settings/kit";
import { Bar, ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Meter } from "@/components/ui/Figures";
import { Pill } from "@/components/ui/Pill";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { cn, dateTime } from "@/lib/format";
import type { Offer, OrgBilling, OrgLimit } from "@/lib/types";

/** "$49.00": money in its own currency. */
function money(cents: number, currency = "USD"): string {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

const price = (o: Offer) => (o.price_cents ? `${money(o.price_cents, o.currency)}${o.interval === "month" ? " a month" : ""}` : "Free");
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long" });
const amount = (v: number | null | undefined) => (v == null ? "Unlimited" : v.toLocaleString());

/** Only the people who manage the organization's billing (its owners) see it. */
function useBilling() {
  const { canOrg, me } = useMe();
  const allowed = canOrg("billing.manage");
  const billing = useQuery({ queryKey: ["org-billing"], queryFn: api.billing.get, enabled: allowed });
  return { allowed, role: me?.org_role ?? undefined, billing };
}

function Gate({ allowed, role, children }: { allowed: boolean; role?: string; children: React.ReactNode }) {
  return allowed ? <>{children}</> : <div className="pt-6"><NoAccess what="see or change the organization's billing" role={role} /></div>;
}

function Loading() {
  return <div className="flex flex-col gap-3 pt-6">{[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>;
}

// ---- Plan and usage ---------------------------------------------------------------------------

export function BillingPlan() {
  const { allowed, role, billing } = useBilling();
  const b = billing.data;
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Plan and usage" description="The organization's plan, shared by all its workspaces: the pages it reads each period, and what it can have." />
      <Gate allowed={allowed} role={role}>
        {billing.isError ? <div className="pt-6"><ErrorNote message="Couldn't load billing." onRetry={() => billing.refetch()} /></div>
          : !b ? <Loading /> : <PlanBody b={b} />}
      </Gate>
    </div>
  );
}

function PlanBody({ b }: { b: OrgBilling }) {
  const pagesLeft = b.pages.limit == null ? null : Math.max(0, b.pages.limit - b.pages.used);
  return (
    <>
      <Section title="Current plan" description={`This period runs from ${day(b.period_start)} to ${day(b.period_end)}, when the plan's pages renew.`}>
        <div className="flex flex-col gap-5 rounded-card border border-border bg-card p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="flex flex-wrap items-center gap-2 text-section font-semibold text-ink">
                {b.plan.name}
                {b.status === "trialing" && b.trial_ends_at ? <Pill tone="warning">Trial until {day(b.trial_ends_at)}</Pill> : <Pill tone="accent">Current</Pill>}
              </p>
              <p className="num text-body text-muted">{price(b.plan)}</p>
              {b.pending_plan && <p className="mt-1 text-meta text-muted">Changes to {b.pending_plan.name} on {day(b.period_end)}.</p>}
            </div>
          </div>
          <Meter label="Pages read this period" used={b.pages.used} limit={b.pages.limit} />
          <p className="text-body text-muted">
            {pagesLeft === null ? "Pages are unlimited on this plan." : pagesLeft > 0 ? `${pagesLeft.toLocaleString()} of the plan's pages left this period.`
              : b.credits.balance > 0 ? "The plan's pages are used: documents are being read on prepaid credits."
                : "The plan's pages are used and there are no prepaid credits: new documents are refused until they renew or you add credits."}
          </p>
        </div>
      </Section>

      <Section title="Prepaid credits" description="Pages you buy in packs. They're used once the plan's pages for the period run out, and they don't expire.">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-border bg-card p-4">
          <p className="num text-title font-semibold text-ink">{b.credits.balance.toLocaleString()} <span className="text-body font-normal text-muted">pages</span></p>
        </div>
      </Section>

      <Section title="By workspace" description="Pages each workspace read this period. They all come out of the same allowance.">
        {b.pages.by_workspace.length === 0 ? <p className="text-body text-muted">No pages read yet this period.</p> : (
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
            {b.pages.by_workspace.map((w) => (
              <li key={w.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-body">
                <span className="min-w-0 truncate text-ink">{w.name}</span>
                <span className="num text-muted">{w.pages.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="What the plan allows" description="With any add-ons and limits agreed with Syruss.">
        <Limits limits={b.limits} />
        {b.addons.length > 0 && (
          <p className="mt-3 text-body text-muted">Add-ons: {b.addons.map((a) => `${a.quantity > 1 ? `${a.quantity} × ` : ""}${a.addon.name}`).join(", ")}.</p>
        )}
      </Section>
    </>
  );
}

function Limits({ limits }: { limits: OrgLimit[] }) {
  return (
    <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-card border border-border bg-border @2xl:grid-cols-2">
      {limits.map((l) => (
        <div key={l.key} className="flex items-baseline justify-between gap-3 bg-card px-4 py-3">
          <dt className="text-body text-muted">{l.label}</dt>
          <dd className="num text-body font-medium text-ink">{amount(l.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

// ---- Buying -----------------------------------------------------------------------------------

export function BillingBuy() {
  const { allowed, role, billing } = useBilling();
  const offers = useQuery({ queryKey: ["org-billing-offers"], queryFn: api.billing.offers, enabled: allowed });
  const b = billing.data;
  const o = offers.data;
  const waiting = b?.requests.filter((r) => r.status === "open") ?? [];
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Plans and credits" description={b?.provider.takes_payments
        ? "Change the plan, add more each month, or buy prepaid pages."
        : "Change the plan, add more each month, or buy prepaid pages. Syruss sends an invoice and switches it on when it's paid."} />
      <Gate allowed={allowed} role={role}>
        {offers.isError ? <div className="pt-6"><ErrorNote message="Couldn't load what's on offer." onRetry={() => offers.refetch()} /></div>
          : !o || !b ? <Loading /> : (
            <>
              {waiting.length > 0 && (
                <div className="mt-6 rounded-card bg-[var(--pill-warning-bg)] px-4 py-3 text-body text-[var(--pill-warning-ink)]">
                  Waiting for Syruss: {waiting.map((r) => `${r.quantity > 1 ? `${r.quantity} × ` : ""}${r.item.name}`).join(", ")}.
                </div>
              )}
              <Section title="Prepaid pages" description="Read beyond the plan's pages. They're kept until used.">
                {o.credits.length === 0 ? <p className="text-body text-muted">No credit packs are on offer yet. Ask Syruss if you need more pages.</p> : (
                  <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">{o.credits.map((c) => <OfferCard key={c.id} offer={c} detail={`${(c.pages ?? 0).toLocaleString()} pages`} quantity />)}</div>
                )}
              </Section>
              <Section title="Plans" description="Limits are for the whole organization (pages) or for each workspace.">
                <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
                  {o.plan.map((p) => (
                    <OfferCard key={p.id} offer={p} current={p.key === b.plan.key}
                      detail={`${amount(p.limits.pages_per_month)} pages a period · ${amount(p.limits.members_per_workspace)} members a workspace`} />
                  ))}
                </div>
              </Section>
              {o.addon.length > 0 && (
                <Section title="Add-ons" description="On top of the plan, each month. Several stack.">
                  <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
                    {o.addon.map((a) => (
                      <OfferCard key={a.id} offer={a} quantity
                        detail={Object.entries(a.limits).map(([k, v]) => `+${amount(v)} ${b.limits.find((l) => l.key === k)?.noun ?? k}`).join(" · ")} />
                    ))}
                  </div>
                </Section>
              )}
            </>
          )}
      </Gate>
    </div>
  );
}

function OfferCard({ offer, detail, current, quantity }: { offer: Offer; detail: string; current?: boolean; quantity?: boolean }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [count, setCount] = useState(1);
  const [note, setNote] = useState("");
  const buy = useMutation({
    mutationFn: () => api.billing.buy({ item_id: offer.id, quantity: count, note: note.trim() || undefined }),
    onSuccess: (started) => {
      if (started.url) window.location.assign(started.url);
      setAsking(false);
      queryClient.invalidateQueries({ queryKey: ["org-billing"] });
    },
  });
  return (
    <div className={cn("flex flex-col gap-3 rounded-card border bg-card p-4", current ? "border-accent" : "border-border")}>
      <div>
        <p className="flex items-center gap-2 text-body font-semibold text-ink">{offer.name}{current && <Pill tone="accent">Current</Pill>}</p>
        <p className="num text-body text-ink">{price(offer)}</p>
        <p className="mt-1 text-meta text-muted">{detail}</p>
        {offer.description && <p className="mt-1 text-meta text-muted">{offer.description}</p>}
      </div>
      {buy.isSuccess && !asking ? <p className="text-body text-positive">{buy.data.message}</p> : current ? null : !asking ? (
        <div><Button variant="secondary" onClick={() => setAsking(true)}>{offer.kind === "plan" ? "Switch to this plan" : "Buy"}</Button></div>
      ) : (
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); buy.mutate(); }}>
          {quantity && <Field label="How many">{(id) => <Input id={id} type="number" min={1} max={1000} value={count} onChange={(e) => setCount(Math.max(1, Number(e.target.value)))} />}</Field>}
          <Field label="Note for Syruss (optional)">{(id) => <Input id={id} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder="Purchase order number, who to invoice" />}</Field>
          {buy.error && <p className="text-body text-negative">{buy.error.message}</p>}
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={buy.isPending}>{buy.isPending ? "Sending…" : `Ask for it (${money(offer.price_cents * count, offer.currency)})`}</Button>
            <Button variant="secondary" onClick={() => setAsking(false)}>Cancel</Button>
          </div>
        </form>
      )}
    </div>
  );
}

// ---- History ----------------------------------------------------------------------------------

const CREDIT_KIND: Record<OrgBilling["credits"]["history"][number]["kind"], string> = {
  purchase: "Bought", grant: "Given by Syruss", usage: "Used by a document", refund: "Refunded", adjustment: "Corrected",
};

export function BillingHistory() {
  const { allowed, role, billing } = useBilling();
  const b = billing.data;
  return (
    <div className="flex flex-col">
      <SettingsHeader title="History" description="Payments, what you asked for, and every change to your prepaid credits." />
      <Gate allowed={allowed} role={role}>
        {!b ? <Loading /> : (
          <>
            <Section title="Payments">
              {b.payments.length === 0 ? <p className="text-body text-muted">No payments yet.</p> : (
                <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
                  {b.payments.map((p, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-body">
                      <span className="num font-medium text-ink">{money(p.amount_cents, p.currency)}</span>
                      <span className="min-w-0 flex-1 truncate text-muted">{p.for ?? "Payment"}{p.quantity > 1 ? ` × ${p.quantity}` : ""}{p.reference ? ` · ${p.reference}` : ""}</span>
                      <span className="text-meta text-muted">{dateTime(p.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Requests">
              {b.requests.length === 0 ? <p className="text-body text-muted">You haven&apos;t asked for anything yet.</p> : (
                <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
                  {b.requests.map((r) => (
                    <li key={r.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-body">
                      <span className="min-w-0 flex-1 truncate text-ink">{r.quantity > 1 ? `${r.quantity} × ` : ""}{r.item.name}{r.note ? <span className="text-muted"> · {r.note}</span> : null}</span>
                      <Pill tone={r.status === "open" ? "warning" : r.status === "done" ? "positive" : "neutral"}>{{ open: "Waiting", done: "Switched on", declined: "Declined" }[r.status]}</Pill>
                      <span className="text-meta text-muted">{dateTime(r.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Prepaid credits" description={`${b.credits.balance.toLocaleString()} pages left.`}>
              {b.credits.history.length === 0 ? <p className="text-body text-muted">No credits yet.</p> : (
                <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
                  {b.credits.history.map((e, i) => (
                    <li key={i} className="flex items-center gap-3 px-4 py-2.5 text-body">
                      <span className={cn("num w-20 flex-none", e.delta > 0 ? "text-positive" : "text-muted")}>{e.delta > 0 ? "+" : ""}{e.delta.toLocaleString()}</span>
                      <span className="min-w-0 flex-1 truncate text-ink">{CREDIT_KIND[e.kind]}{e.reason ? <span className="text-muted"> · {e.reason}</span> : null}</span>
                      <span className="text-meta text-muted">{dateTime(e.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </>
        )}
      </Gate>
    </div>
  );
}
