"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { InvoiceEditor, InvoiceTable } from "@/components/billing/Invoices";
import { Label, StaffForm } from "@/components/billing/StaffForm";
import { Fact, ReasonAction, field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { METHOD_LABEL, amount, money, price } from "@/lib/billing";
import { cn, dateTime } from "@/lib/format";
import type { CatalogItem, LimitInfo, OrgBilling } from "@/lib/types";

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—");

export default function OrganizationBilling() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const org = useQuery({ queryKey: ["billing", "organization", tenantId], queryFn: () => api.billing.organization(tenantId) });
  const setup = useQuery({ queryKey: ["billing", "setup"], queryFn: api.billing.setup, staleTime: 300_000 });
  const catalog = useQuery({ queryKey: ["billing", "catalog"], queryFn: () => api.billing.catalog() });
  const d = org.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader back={{ href: "/billing", label: "← Billing" }} title={d ? d.organization.name : "Organization"}
        subtitle={d ? `${d.subscription.plan.name} plan · period ${day(d.subscription.period_start)} to ${day(d.subscription.period_end)}` : "Loading…"} />
      {org.isError && <ErrorNote message="Couldn't load this organization's billing." onRetry={() => org.refetch()} />}
      {!d || !setup.data || !catalog.data ? <Bar className="h-96 w-full rounded-card" /> : (
        <Body d={d} limits={setup.data.limits} catalog={catalog.data} />
      )}
    </div>
  );
}

function Body({ d, limits, catalog }: { d: OrgBilling; limits: LimitInfo[]; catalog: CatalogItem[] }) {
  const me = useStaff();
  const sub = d.subscription;
  const unpaid = sub.plan.price_cents > 0 && (!sub.paid_until || sub.paid_until < sub.period_end);
  return (
    <>
      <Panel>
        <dl className="grid grid-cols-2 gap-4 @3xl:grid-cols-4">
          <Fact label="Plan">{sub.plan.name} <span className="text-muted">v{sub.plan.version} · {price(sub.plan)}</span></Fact>
          <Fact label="Standing">
            {sub.status === "trialing" ? <Pill tone="warning">Trial until {day(sub.trial_ends_at)}</Pill>
              : sub.status === "past_due" ? <Pill tone="negative">Renewal past due</Pill>
                : unpaid ? <Pill tone="negative">Not paid this period</Pill> : <Pill tone="positive">{sub.status === "active" ? "Active" : "Canceled"}</Pill>}
          </Fact>
          <Fact label="Paid until">{day(sub.paid_until)}</Fact>
          <Fact label="From the next period">{sub.pending_plan ? `${sub.pending_plan.name} plan` : "No change"}</Fact>
          <Fact label="Pages this period">{d.usage.pages_used.toLocaleString()} of {amount(d.usage.pages_limit)}</Fact>
          <Fact label="Prepaid credits">{d.credits.balance.toLocaleString()} pages</Fact>
          <Fact label="Periods start on">day {sub.anchor_day} of the month</Fact>
          <Fact label="Organization">{d.organization.slug} · {d.organization.status}</Fact>
        </dl>
      </Panel>

      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-6">
          <LimitTable d={d} limits={limits} />
          <Panel title="Pages by workspace this period">
            {d.usage.by_workspace.length === 0 ? <p className="text-body text-muted">No pages read yet this period.</p> : (
              <ul className="flex flex-col divide-y divide-rule">
                {d.usage.by_workspace.map((w) => (
                  <li key={w.id} className="flex items-center justify-between gap-2 py-2 text-body">
                    <span className="truncate text-ink">{w.name ?? "A deleted workspace"}</span><span className="num text-muted">{w.pages.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Prepaid credits" aside={<span className="num">{d.credits.balance.toLocaleString()} left</span>}>
            {d.credits.history.length === 0 ? <p className="text-body text-muted">None bought or given yet.</p> : (
              <ul className="flex flex-col divide-y divide-rule">
                {d.credits.history.map((e) => (
                  <li key={e.id} className="flex items-center gap-2 py-2 text-body">
                    <span className={cn("num w-20 flex-none", e.delta > 0 ? "text-positive" : "text-muted")}>{e.delta > 0 ? "+" : ""}{e.delta.toLocaleString()}</span>
                    <span className="min-w-0 flex-1 truncate text-ink">{e.kind === "usage" ? "Used by a document" : `${e.kind[0].toUpperCase()}${e.kind.slice(1)}${e.reason ? `: ${e.reason}` : ""}`}</span>
                    <span className="text-meta text-muted">{dateTime(e.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Invoices" bodyClassName="p-0 pt-2">
            {d.invoices.length === 0 ? <p className="px-4 pb-4 text-body text-muted">None yet.</p> : <InvoiceTable rows={d.invoices} />}
          </Panel>
          <Panel title="Payments recorded">
            {d.payments.length === 0 ? <p className="text-body text-muted">No payment recorded yet.</p> : (
              <ul className="flex flex-col divide-y divide-rule">
                {d.payments.map((p) => (
                  <li key={p.id} className="flex flex-wrap items-center gap-2 py-2 text-body">
                    <span className="num font-medium text-ink">{money(p.amount_cents, p.currency)}</span>
                    <span className="min-w-0 flex-1 truncate text-muted">
                      {p.invoice_id ? <Link href={`/billing/invoices/${p.invoice_id}`} className="hover:underline">{p.invoice}</Link>
                        : `${catalog.find((i) => i.id === p.item_id)?.name ?? "No item"}${(p.quantity ?? 1) > 1 ? ` × ${p.quantity}` : ""}`}
                      {` · ${METHOD_LABEL[p.method] ?? p.method}`}{p.reference ? ` · ${p.reference}` : ""}
                    </span>
                    <span className="text-meta text-muted">{p.recorded_by_name} · {dateTime(p.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        {me.can("billing.manage") ? <Changes d={d} limits={limits} catalog={catalog} /> : (
          <Panel title="Changes"><p className="text-body text-muted">Your platform role sees billing; the Billing role changes it.</p></Panel>
        )}
      </div>
    </>
  );
}

function LimitTable({ d, limits }: { d: OrgBilling; limits: LimitInfo[] }) {
  return (
    <Panel title="Limits and where each comes from" bodyClassName="p-0 pt-2">
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full min-w-[640px] text-body">
          <thead>
            <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
              <th scope="col">Limit</th><th scope="col" className="text-right">Plan</th><th scope="col" className="text-right">Add-ons</th>
              <th scope="col">Custom</th><th scope="col" className="text-right">Now</th>
            </tr>
          </thead>
          <tbody className="[&>tr]:border-b [&>tr]:border-rule [&>tr:last-child]:border-0 [&_td]:px-4 [&_td]:py-2">
            {limits.map((l) => {
              const added = d.limits.addons.reduce((n, a) => n + (a.addon.limits[l.key] ?? 0) * a.quantity, 0);
              const custom = d.limits.overrides.filter((o) => o.key === l.key);
              return (
                <tr key={l.key}>
                  <td className="text-ink">{l.label}</td>
                  <td className="num text-right text-muted">{amount(d.limits.plan[l.key])}</td>
                  <td className="num text-right text-muted">{added ? `+${added.toLocaleString()}` : "—"}</td>
                  <td className="text-muted">
                    {custom.length === 0 ? "—" : custom.map((o) => (
                      <span key={o.id} className="block">{amount(o.value)}{o.workspace ? ` in ${o.workspace}` : ""}{o.expires_at ? `, until ${day(o.expires_at)}` : ""}</span>
                    ))}
                  </td>
                  <td className="num text-right font-medium text-ink">{amount(d.limits.effective[l.key])}{l.scope === "workspace" ? " each" : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

type Change = "plan" | "addon" | "override" | "trial" | "credits" | "invoice";

const CHANGES: { key: Change; label: string }[] = [
  { key: "invoice", label: "New invoice" },
  { key: "plan", label: "Change the plan" },
  { key: "addon", label: "Add-ons" },
  { key: "credits", label: "Give or correct credits" },
  { key: "override", label: "Custom limit" },
  { key: "trial", label: "Start a trial" },
];

function Changes({ d, limits, catalog }: { d: OrgBilling; limits: LimitInfo[]; catalog: CatalogItem[] }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<Change | null>(null);
  const id = d.organization.id;
  const onSale = useMemo(() => catalog.filter((i) => i.status === "published"), [catalog]);
  const plans = onSale.filter((i) => i.kind === "plan");
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <Panel title="Changes">
        <div className="flex flex-wrap gap-2">
          {CHANGES.map((c) => <Button key={c.key} size="sm" variant={open === c.key ? "primary" : "secondary"} onClick={() => setOpen(open === c.key ? null : c.key)}>{c.label}</Button>)}
        </div>
        {open && (
          <div className="mt-4 rounded-control bg-raised p-3">
            {open === "plan" && <PlanForm id={id} plans={plans} current={d.subscription.plan.id} onDone={() => setOpen(null)} />}
            {open === "addon" && <AddonForm id={id} addons={onSale.filter((i) => i.kind === "addon")} have={d.limits.addons} onDone={() => setOpen(null)} />}
            {open === "credits" && <CreditsForm id={id} onDone={() => setOpen(null)} />}
            {open === "override" && <OverrideForm d={d} limits={limits} onDone={() => setOpen(null)} />}
            {open === "trial" && <TrialForm id={id} plans={plans.filter((p) => p.price_cents > 0)} onDone={() => setOpen(null)} />}
            {open === "invoice" && <InvoiceEditor tenantId={id} onDone={() => setOpen(null)} />}
          </div>
        )}
      </Panel>
      <Panel title="Custom limits">
        {d.limits.overrides.length === 0 ? <p className="text-body text-muted">None: every limit comes from the plan and its add-ons.</p> : (
          <ul className="flex flex-col divide-y divide-rule">
            {d.limits.overrides.map((o) => (
              <li key={o.id} className="flex flex-wrap items-center gap-2 py-2 text-body">
                <span className="min-w-0 flex-1">
                  <span className="text-ink">{limits.find((l) => l.key === o.key)?.label ?? o.key}: {amount(o.value)}{o.workspace ? ` in ${o.workspace}` : ""}</span>
                  <span className="block text-meta text-muted">{o.reason} · {o.set_by}{o.expires_at ? ` · until ${day(o.expires_at)}` : ""}</span>
                </span>
                <ReasonAction label="Remove" busyLabel="Removing…" confirmLabel="Remove" variant="danger" onRun={(r) => api.billing.removeOverride(d.organization.id, o.id, r).then(() => queryClient.invalidateQueries({ queryKey: ["billing"] }))} />
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Billing details" aside={d.billing_details_missing.length ? <Pill tone="negative">Incomplete</Pill> : undefined}>
        {!d.billing_details ? <p className="text-body text-muted">Not given yet: invoices can&apos;t be issued to this organization until its owners add them.</p> : (
          <div className="flex flex-col gap-0.5 text-body">
            <span className="font-medium text-ink">{d.billing_details.legal_name}</span>
            <span className="text-ink">{[d.billing_details.address_line1, d.billing_details.address_line2, d.billing_details.city, d.billing_details.country].filter(Boolean).join(", ")}</span>
            <span className="text-muted">{d.billing_details.email}{d.billing_details.tax_id ? ` · Tax ID ${d.billing_details.tax_id}` : ""}{d.billing_details.po_number ? ` · PO ${d.billing_details.po_number}` : ""}</span>
            {d.billing_details_missing.length > 0 && <span className="text-negative">Missing: {d.billing_details_missing.join(", ")}</span>}
          </div>
        )}
      </Panel>
    </div>
  );
}

function PlanForm({ id, plans, current, onDone }: { id: string; plans: CatalogItem[]; current: string; onDone: () => void }) {
  const [plan, setPlan] = useState(plans.find((p) => p.id !== current)?.id ?? "");
  const [later, setLater] = useState(false);
  return (
    <StaffForm title="Change the plan" submit="Change it" busy="Changing…" ready={!!plan} onDone={onDone}
      onRun={(reason) => api.billing.change(id, "plan", { plan_id: plan, at_period_end: later, reason })}>
      <Label text="Plan">
        <select value={plan} onChange={(e) => setPlan(e.target.value)} className={field}>
          {plans.map((p) => <option key={p.id} value={p.id}>{p.name} v{p.version} ({price(p)}){p.id === current ? " (now)" : ""}</option>)}
        </select>
      </Label>
      <label className="flex items-center gap-2 text-body text-ink"><input type="checkbox" checked={later} onChange={(e) => setLater(e.target.checked)} /> From the next period (a downgrade)</label>
    </StaffForm>
  );
}

function AddonForm({ id, addons, have, onDone }: { id: string; addons: CatalogItem[]; have: OrgBilling["limits"]["addons"]; onDone: () => void }) {
  const [addon, setAddon] = useState(addons[0]?.id ?? have[0]?.addon.id ?? "");
  const current = have.find((a) => a.addon.id === addon)?.quantity ?? 0;
  const [quantity, setQuantity] = useState(current || 1);
  const choices = [...addons, ...have.map((a) => a.addon).filter((a) => !addons.some((x) => x.id === a.id))];
  if (!choices.length) return <p className="text-body text-muted">No add-on is on sale. Create one under Plans and packs.</p>;
  return (
    <StaffForm title="How many of an add-on" submit={quantity ? "Set it" : "Remove it"} busy="Saving…" ready={!!addon} onDone={onDone}
      onRun={(reason) => api.billing.change(id, "addons", { addon_id: addon, quantity, reason })}>
      <Label text="Add-on">
        <select value={addon} onChange={(e) => setAddon(e.target.value)} className={field}>
          {choices.map((a) => <option key={a.id} value={a.id}>{a.name} ({price(a)})</option>)}
        </select>
      </Label>
      <Label text={`Quantity (now ${current}; 0 removes it)`}>
        <input type="number" min={0} value={quantity} onChange={(e) => setQuantity(Math.max(0, Number(e.target.value)))} className={field} />
      </Label>
    </StaffForm>
  );
}

function CreditsForm({ id, onDone }: { id: string; onDone: () => void }) {
  const [pages, setPages] = useState(100);
  const [kind, setKind] = useState<"grant" | "refund" | "adjustment">("grant");
  return (
    <StaffForm title="Give or correct prepaid credits" submit={pages >= 0 ? `Add ${pages.toLocaleString()} pages` : `Take back ${(-pages).toLocaleString()} pages`}
      busy="Saving…" ready={pages !== 0} onDone={onDone} onRun={(reason) => api.billing.change(id, "credits", { pages, kind, reason })}>
      <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
        <Label text="Pages (negative takes back)"><input type="number" value={pages} onChange={(e) => setPages(Number(e.target.value))} className={field} /></Label>
        <Label text="Kind">
          <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={field}>
            <option value="grant">Given (goodwill, a deal)</option><option value="refund">Refund (pages we got wrong)</option><option value="adjustment">Correction</option>
          </select>
        </Label>
      </div>
      <p className="text-meta text-muted">Credits a customer paid for: record the payment instead, which adds them.</p>
    </StaffForm>
  );
}

function OverrideForm({ d, limits, onDone }: { d: OrgBilling; limits: LimitInfo[]; onDone: () => void }) {
  const [key, setKey] = useState(limits[0]?.key ?? "");
  const [value, setValue] = useState<string>("");
  const [workspace, setWorkspace] = useState("");
  const [until, setUntil] = useState("");
  const limit = limits.find((l) => l.key === key);
  return (
    <StaffForm title="Set a limit by hand" submit="Set it" busy="Saving…" ready={!!key} onDone={onDone}
      onRun={(reason) => api.billing.change(d.organization.id, "overrides", {
        key, value: value === "" ? null : Number(value), template_id: workspace || null, expires_at: until ? new Date(until).toISOString() : null, reason })}>
      <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
        <Label text="Limit">
          <select value={key} onChange={(e) => { setKey(e.target.value); setWorkspace(""); }} className={field}>
            {limits.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
          </select>
        </Label>
        <Label text="Value (empty: unlimited)"><input type="number" min={0} value={value} onChange={(e) => setValue(e.target.value)} className={field} placeholder="Unlimited" /></Label>
        {limit?.scope === "workspace" && (
          <Label text="For">
            <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} className={field}>
              <option value="">Every workspace</option>
              {d.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </Label>
        )}
        <Label text="Until (optional)"><input type="date" value={until} onChange={(e) => setUntil(e.target.value)} className={field} /></Label>
      </div>
    </StaffForm>
  );
}

function TrialForm({ id, plans, onDone }: { id: string; plans: CatalogItem[]; onDone: () => void }) {
  const [plan, setPlan] = useState(plans[0]?.id ?? "");
  const [days, setDays] = useState(14);
  return (
    <StaffForm title="Start a trial" submit={`Start ${days} days`} busy="Starting…" ready={!!plan} onDone={onDone}
      onRun={(reason) => api.billing.change(id, "trial", { plan_id: plan, days, reason })}>
      <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
        <Label text="Plan">
          <select value={plan} onChange={(e) => setPlan(e.target.value)} className={field}>{plans.map((p) => <option key={p.id} value={p.id}>{p.name} ({price(p)})</option>)}</select>
        </Label>
        <Label text="Days"><input type="number" min={1} max={90} value={days} onChange={(e) => setDays(Number(e.target.value))} className={field} /></Label>
      </div>
      <p className="text-meta text-muted">Unless a payment for the plan is recorded by then, the organization goes back to Free.</p>
    </StaffForm>
  );
}

