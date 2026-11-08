"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { CatalogEditor } from "@/components/billing/CatalogEditor";
import { InvoiceList } from "@/components/billing/Invoices";
import { PaymentsQueue } from "@/components/billing/PaymentsQueue";
import { BillingSettings } from "@/components/billing/Settings";
import { Label, StaffForm } from "@/components/billing/StaffForm";
import { ReasonAction, field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ITEM_STATUS, KIND_LABEL, amount, itemSummary, price } from "@/lib/billing";
import { cn } from "@/lib/format";
import type { CatalogItem, CatalogKind, OrgBillingRow } from "@/lib/types";

const TABS = [
  { key: "organizations", label: "Organizations" },
  { key: "invoices", label: "Invoices" },
  { key: "payments", label: "Payments to check" },
  { key: "catalog", label: "Plans and packs" },
  { key: "settings", label: "Settings" },
] as const;

export default function BillingPage() {
  return <Suspense><Billing /></Suspense>;
}

function Billing() {
  const tab = useSearchParams().get("tab") ?? "organizations";
  const waiting = useQuery({ queryKey: ["billing", "submissions", "submitted"], queryFn: () => api.billing.submissions("submitted"), refetchInterval: 30_000 });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Billing" subtitle="What each organization has, owes and paid; the slips customers sent to check; what is on sale; and Syruss's own billing details." />
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Show">
        {TABS.map((t) => (
          <Link key={t.key} href={`/billing?tab=${t.key}`} aria-current={tab === t.key ? "page" : undefined}
            className={cn("inline-flex h-8 items-center gap-1.5 rounded-control px-3 text-body transition-colors", tab === t.key ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}>
            {t.label}
            {t.key === "payments" && !!waiting.data?.length && <span className="num text-meta text-[var(--pill-warning-ink)]">{waiting.data.length}</span>}
          </Link>
        ))}
      </div>
      {tab === "catalog" ? <Catalog /> : tab === "invoices" ? <InvoiceList /> : tab === "payments" ? <PaymentsQueue />
        : tab === "settings" ? <BillingSettings /> : <Organizations />}
    </div>
  );
}

function Meter({ used, limit }: { used: number; limit: number | null }) {
  const share = limit ? Math.min(1, used / limit) : 0;
  return (
    <span className="flex min-w-32 flex-col gap-1">
      <span className="num text-body text-ink">{used.toLocaleString()} <span className="text-muted">/ {amount(limit)}</span></span>
      {limit != null && (
        <span className="h-1.5 rounded-full bg-raised" aria-hidden>
          <span className={cn("block h-full rounded-full", share >= 1 ? "bg-negative" : share >= 0.8 ? "bg-[var(--pill-warning-ink)]" : "bg-accent")}
            style={{ width: `${Math.max(2, share * 100)}%` }} />
        </span>
      )}
    </span>
  );
}

function standing(o: OrgBillingRow): { label: string; tone: "positive" | "warning" | "neutral" | "negative" } {
  if (o.subscription_status === "trialing") return { label: `Trial until ${new Date(o.trial_ends_at!).toLocaleDateString()}`, tone: "warning" };
  if (o.subscription_status === "past_due") return { label: "Past due", tone: "negative" };
  if (o.plan_key !== "free" && (!o.paid_until || o.paid_until < o.period_end)) return { label: "Not paid this period", tone: "negative" };
  return { label: o.subscription_status === "active" ? "Active" : "Canceled", tone: o.subscription_status === "active" ? "positive" : "neutral" };
}

function Organizations() {
  const [q, setQ] = useState("");
  const orgs = useQuery({ queryKey: ["billing", "organizations", q], queryFn: () => api.billing.organizations(q || undefined) });
  const follow = useQuery({ queryKey: ["billing", "follow-ups"], queryFn: () => api.billing.followUps(7) });
  return (
    <>
      {!!follow.data?.length && (
        <Panel title="Follow up this week" aside={<span className="num">{follow.data.length}</span>}>
          <ul className="flex flex-col divide-y divide-rule">
            {follow.data.map((f) => (
              <li key={f.tenant_id} className="flex flex-wrap items-center gap-2 py-2 text-body">
                <Link href={`/billing/${f.tenant_id}`} className="min-w-0 flex-1 truncate text-ink hover:underline">{f.organization?.name ?? f.tenant_id}</Link>
                <span className="text-muted">{f.plan}</span>
                <Pill tone="warning">{f.status === "trialing" ? `Trial ends ${new Date(f.trial_ends_at!).toLocaleDateString(undefined, { day: "numeric", month: "short" })}`
                  : f.status === "past_due" ? "Renewal past due" : "Payment not recorded for this period"}</Pill>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <Panel bodyClassName="p-0">
        <div className="p-4 pb-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an organization by name" aria-label="Find an organization" className={cn(field, "w-full @2xl:w-80")} />
        </div>
        {orgs.isError && <div className="p-4"><ErrorNote message="Couldn't load the organizations." onRetry={() => orgs.refetch()} /></div>}
        {orgs.isLoading ? (
          <div className="flex flex-col gap-3 p-4">{Array.from({ length: 6 }, (_, i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : !orgs.data?.length ? (
          <div className="p-4"><Empty title="No organizations match" /></div>
        ) : (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full min-w-[820px] text-body">
              <thead>
                <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
                  <th scope="col">Organization</th><th scope="col">Plan</th><th scope="col">Pages this period</th>
                  <th scope="col" className="text-right">Credits</th><th scope="col">Renews</th><th scope="col">Invoices</th>
                </tr>
              </thead>
              <tbody className="[&>tr]:border-b [&>tr]:border-rule [&>tr:last-child]:border-0 [&_td]:px-4 [&_td]:py-2.5">
                {orgs.data.map((o) => {
                  const s = standing(o);
                  return (
                    <tr key={o.id} className="hover:bg-raised">
                      <td><Link href={`/billing/${o.id}`} className="font-medium text-ink hover:underline">{o.name}</Link><span className="block text-meta text-muted">{o.slug}</span></td>
                      <td><span className="text-ink">{o.plan}</span> <Pill tone={s.tone} className="ml-1">{s.label}</Pill></td>
                      <td><Meter used={o.pages_used} limit={o.pages_limit} /></td>
                      <td className="num text-right">{o.credits.toLocaleString()}</td>
                      <td className="text-muted">{new Date(o.period_end).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</td>
                      <td className="flex flex-wrap gap-1">
                        {o.invoices_overdue ? <Pill tone="negative">{o.invoices_overdue} overdue</Pill> : null}
                        {o.invoices_waiting - o.invoices_overdue > 0 ? <Pill tone="warning">{o.invoices_waiting - o.invoices_overdue} waiting</Pill> : null}
                        {o.drafts ? <Pill tone="neutral">{o.drafts} draft{o.drafts === 1 ? "" : "s"}</Pill> : null}
                        {!o.invoices_waiting && !o.drafts && <span className="text-muted">All paid</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

type Editing = { new: CatalogKind } | { edit: CatalogItem } | { version: CatalogItem } | null;

function Catalog() {
  const me = useStaff();
  const can = me.can("billing.manage");
  const setup = useQuery({ queryKey: ["billing", "setup"], queryFn: api.billing.setup, staleTime: 300_000 });
  const items = useQuery({ queryKey: ["billing", "catalog"], queryFn: () => api.billing.catalog() });
  const [editing, setEditing] = useState<Editing>(null);
  const labels = useMemo(() => Object.fromEntries((setup.data?.limits ?? []).map((l) => [l.key, l])), [setup.data]);
  const grouped = useMemo(() => {
    const byKind: Record<CatalogKind, Record<string, CatalogItem[]>> = { plan: {}, addon: {}, credits: {} };
    for (const i of items.data ?? []) (byKind[i.kind][i.key] ??= []).push(i);
    return byKind;
  }, [items.data]);

  if (items.isError) return <ErrorNote message="Couldn't load the catalog." onRetry={() => items.refetch()} />;
  if (!items.data || !setup.data) return <Bar className="h-96 w-full rounded-card" />;
  return (
    <>
      {editing && (
        <Panel><CatalogEditor mode={editing} limits={setup.data.limits} onDone={() => setEditing(null)} /></Panel>
      )}
      {(["plan", "addon", "credits"] as const).map((kind) => (
        <Panel key={kind} title={KIND_LABEL[kind].many}
          aside={can && !editing && <Button size="sm" onClick={() => setEditing({ new: kind })}>New {KIND_LABEL[kind].one.toLowerCase()}</Button>}>
          {Object.keys(grouped[kind]).length === 0 ? (
            <p className="text-body text-muted">{kind === "credits" ? "No credit packs yet: without one, nobody can read past their plan's pages." : `No ${KIND_LABEL[kind].many.toLowerCase()} yet.`}</p>
          ) : (
            <ul className="flex flex-col divide-y divide-rule">
              {Object.entries(grouped[kind]).map(([key, versions]) => (
                <li key={key} className="flex flex-col gap-2 py-3">
                  {versions.map((v) => (
                    <div key={v.id} className={cn("flex flex-wrap items-center gap-2 text-body", v.status === "archived" && "opacity-70")}>
                      <span className="min-w-0 flex-1">
                        <span className="font-medium text-ink">{v.name}</span>
                        <span className="text-muted"> · {key} v{v.version} · {price(v)}{!v.public && " · staff only"}</span>
                        <span className="block text-meta text-muted">{itemSummary(v, labels)}</span>
                      </span>
                      <Pill tone={ITEM_STATUS[v.status].tone}>{ITEM_STATUS[v.status].label}</Pill>
                      {v.organizations > 0 && <span className="num text-meta text-muted">{v.organizations} organization{v.organizations === 1 ? "" : "s"}</span>}
                      {can && v.status === "draft" && (
                        <>
                          <Button size="sm" onClick={() => setEditing({ edit: v })}>Change</Button>
                          <ReasonAction label="Put on sale" busyLabel="Publishing…" confirmLabel="Publish: it can't change after" onRun={(r) => api.billing.publish(v.id, r).then(() => items.refetch())} />
                        </>
                      )}
                      {can && v.status === "published" && (
                        <>
                          <Button size="sm" onClick={() => setEditing({ version: v })}>New version</Button>
                          <ReasonAction label="Take off sale" busyLabel="Archiving…" confirmLabel="Archive" variant="danger" onRun={(r) => api.billing.archive(v.id, r).then(() => items.refetch())} />
                        </>
                      )}
                      {can && kind === "plan" && v.organizations > 0 && <MovePlan from={v} plans={(items.data ?? []).filter((p) => p.kind === "plan" && p.status === "published" && p.id !== v.id)} />}
                    </div>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      ))}
    </>
  );
}

function MovePlan({ from, plans }: { from: CatalogItem; plans: CatalogItem[] }) {
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState(plans[0]?.id ?? "");
  const [later, setLater] = useState(true);
  if (!plans.length) return null;
  if (!open) return <Button size="sm" onClick={() => setOpen(true)}>Move its organizations…</Button>;
  return (
    <div className="basis-full rounded-control bg-raised p-3">
      <StaffForm title={`Move every organization on ${from.name} v${from.version}`} submit="Move them" busy="Moving…" onDone={() => setOpen(false)} ready={!!to}
        onRun={(reason) => api.billing.move(from.id, { to_id: to, at_period_end: later, reason })}>
        <Label text="To">
          <select value={to} onChange={(e) => setTo(e.target.value)} className={field}>
            {plans.map((p) => <option key={p.id} value={p.id}>{p.name} (v{p.version}, {price(p)})</option>)}
          </select>
        </Label>
        <label className="flex items-center gap-2 text-body text-ink"><input type="checkbox" checked={later} onChange={(e) => setLater(e.target.checked)} /> From each one&apos;s next period</label>
      </StaffForm>
    </div>
  );
}

