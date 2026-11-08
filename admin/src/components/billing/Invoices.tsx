"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Label, MoneyInput, StaffForm } from "@/components/billing/StaffForm";
import { field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, Empty, ErrorNote, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { KIND_OF_INVOICE, day, invoiceStatus, money, price } from "@/lib/billing";
import { cn } from "@/lib/format";
import type { CatalogItem, Invoice } from "@/lib/types";

const FILTERS = [
  { key: "all", label: "All" }, { key: "open", label: "Waiting" }, { key: "overdue", label: "Overdue" }, { key: "paid", label: "Paid" },
  { key: "draft", label: "Drafts" }, { key: "uncollectible", label: "Not paid" }, { key: "void", label: "Void" },
] as const;

/** Every invoice, by state or number, with a CSV for the accountants and new invoices for custom deals. */
export function InvoiceList() {
  const can = useStaff().can("billing.manage");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [q, setQ] = useState("");
  const [making, setMaking] = useState(false);
  const params = filter === "overdue" ? { overdue: "true" } : filter === "all" ? {} : { status: filter };
  const list = useQuery({ queryKey: ["billing", "invoices", filter, q], queryFn: () => api.billing.invoices({ ...params, q: q || undefined }) });
  return (
    <>
      {making && <Panel><InvoiceEditor onDone={() => setMaking(false)} /></Panel>}
      <Panel bodyClassName="p-0">
        <div className="flex flex-wrap items-center gap-2 p-4 pb-2">
          <div className="flex flex-wrap gap-1" role="group" aria-label="Which invoices">
            {FILTERS.map((f) => <Button key={f.key} size="sm" variant={filter === f.key ? "primary" : "secondary"} onClick={() => setFilter(f.key)}>{f.label}</Button>)}
          </div>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find by number" aria-label="Find an invoice by number" className={cn(field, "w-full @2xl:ml-auto @2xl:w-56")} />
          {can && !making && <Button size="sm" onClick={() => setMaking(true)}>New invoice</Button>}
        </div>
        {list.isError && <div className="p-4"><ErrorNote message="Couldn't load the invoices." onRetry={() => list.refetch()} /></div>}
        {!list.data ? <div className="p-4"><Bar className="h-40 w-full" /></div> : list.data.length === 0 ? (
          <div className="p-4"><Empty title="No invoices here" /></div>
        ) : <InvoiceTable rows={list.data} showOrg />}
        <p className="flex flex-wrap gap-3 px-4 py-3 text-meta text-muted">
          Download for the accountants:
          <a className="hover:underline" href={api.billing.exportUrl("invoices")}>Invoices (CSV)</a>
          <a className="hover:underline" href={api.billing.exportUrl("payments")}>Payments (CSV)</a>
          <a className="hover:underline" href={api.billing.exportUrl("credit_notes")}>Credit notes (CSV)</a>
        </p>
      </Panel>
    </>
  );
}

export function InvoiceTable({ rows, showOrg }: { rows: Invoice[]; showOrg?: boolean }) {
  return (
    <div className="overflow-x-auto scrollbar-thin">
      <table className="w-full min-w-[760px] text-body">
        <thead>
          <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
            <th scope="col">Invoice</th>{showOrg && <th scope="col">Organization</th>}<th scope="col">Issued</th><th scope="col">Due</th>
            <th scope="col" className="text-right">Total</th><th scope="col" className="text-right">Owed</th><th scope="col">State</th>
          </tr>
        </thead>
        <tbody className="[&>tr]:border-b [&>tr]:border-rule [&>tr:last-child]:border-0 [&_td]:px-4 [&_td]:py-2.5">
          {rows.map((i) => {
            const s = invoiceStatus(i);
            return (
              <tr key={i.id} className="hover:bg-raised">
                <td><Link href={`/billing/invoices/${i.id}`} className="font-medium text-ink hover:underline">{i.number ?? "Draft"}</Link>
                  <span className="block text-meta text-muted">{KIND_OF_INVOICE[i.kind]}</span></td>
                {showOrg && <td className="text-ink">{i.organization?.name ?? i.tenant_id}</td>}
                <td className="text-muted">{day(i.issued_at)}</td>
                <td className="text-muted">{day(i.due_at)}</td>
                <td className="num text-right text-ink">{money(i.total_cents, i.currency)}</td>
                <td className="num text-right text-ink">{i.amount_due_cents ? money(i.amount_due_cents, i.currency) : "—"}</td>
                <td><Pill tone={s.tone}>{s.label}</Pill></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type LineDraft = { item_id: string; description: string; cents: number; quantity: number };

/** An invoice for a custom deal: catalog items and lines written here, as a draft or issued straight away. */
export function InvoiceEditor({ tenantId, onDone }: { tenantId?: string; onDone: () => void }) {
  const router = useRouter();
  const orgs = useQuery({ queryKey: ["billing", "organizations", ""], queryFn: () => api.billing.organizations(), enabled: !tenantId });
  const catalog = useQuery({ queryKey: ["billing", "catalog"], queryFn: () => api.billing.catalog() });
  const [tenant, setTenant] = useState(tenantId ?? "");
  const [lines, setLines] = useState<LineDraft[]>([{ item_id: "", description: "", cents: 0, quantity: 1 }]);
  const [note, setNote] = useState("");
  const [issue, setIssue] = useState(true);
  const onSale: CatalogItem[] = (catalog.data ?? []).filter((i) => i.status === "published");
  const set = (n: number, patch: Partial<LineDraft>) => setLines((ls) => ls.map((l, i) => (i === n ? { ...l, ...patch } : l)));
  const ready = !!tenant && lines.every((l) => l.item_id || (l.description.trim() && l.cents >= 0)) && lines.length > 0;
  return (
    <StaffForm title="New invoice" submit={issue ? "Issue it" : "Save as a draft"} busy="Saving…" ready={ready} onDone={onDone}
      onRun={async (reason) => {
        const made = await api.billing.createInvoice({
          tenant_id: tenant, note: note.trim() || undefined, issue, reason,
          lines: lines.map((l) => (l.item_id ? { item_id: l.item_id, quantity: l.quantity } : { description: l.description.trim(), unit_cents: l.cents, quantity: l.quantity })),
        });
        router.push(`/billing/invoices/${made.id}`);
        return made;
      }}>
      {!tenantId && (
        <Label text="Organization">
          <select value={tenant} onChange={(e) => setTenant(e.target.value)} className={field}>
            <option value="">Choose…</option>
            {(orgs.data ?? []).map((o) => <option key={o.id} value={o.id}>{o.name} ({o.slug})</option>)}
          </select>
        </Label>
      )}
      <div className="flex flex-col gap-2">
        {lines.map((l, n) => (
          <div key={n} className="grid grid-cols-1 gap-2 rounded-control border border-border p-2 @2xl:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_7rem_5rem_auto]">
            <select value={l.item_id} onChange={(e) => set(n, { item_id: e.target.value })} className={field} aria-label="Catalog item">
              <option value="">A line written here</option>
              {onSale.map((i) => <option key={i.id} value={i.id}>{i.name} ({price(i)})</option>)}
            </select>
            <input value={l.description} onChange={(e) => set(n, { description: e.target.value })} disabled={!!l.item_id} placeholder="What it is"
              aria-label="Description" className={field} />
            {l.item_id ? <span className="self-center text-meta text-muted">Catalog price</span> : <MoneyInput cents={l.cents} onChange={(c) => set(n, { cents: c })} />}
            <input type="number" min={1} value={l.quantity} onChange={(e) => set(n, { quantity: Math.max(1, Number(e.target.value)) })} aria-label="Quantity" className={field} />
            <Button size="sm" variant="ghost" disabled={lines.length === 1} onClick={() => setLines((ls) => ls.filter((_, i) => i !== n))}>Remove</Button>
          </div>
        ))}
        <div><Button size="sm" onClick={() => setLines((ls) => [...ls, { item_id: "", description: "", cents: 0, quantity: 1 }])}>Add a line</Button></div>
      </div>
      <Label text="Note printed on the invoice (optional)"><input value={note} onChange={(e) => setNote(e.target.value)} className={field} placeholder="Purchase order 1234" /></Label>
      <label className="flex items-center gap-2 text-body text-ink"><input type="checkbox" checked={issue} onChange={(e) => setIssue(e.target.checked)} /> Issue it now (numbered, emailed, fixed)</label>
    </StaffForm>
  );
}
