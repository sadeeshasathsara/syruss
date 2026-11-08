"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Label, StaffForm } from "@/components/billing/StaffForm";
import { ReasonAction, field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import type { BillingSettingsPage, SellerSettings } from "@/lib/types";

/** Syruss as the seller: who invoices are from, how they are numbered and paid, the bank accounts and the tax rates. */
export function BillingSettings() {
  const page = useQuery({ queryKey: ["billing", "settings"], queryFn: api.billing.settings });
  if (page.isError) return <ErrorNote message="Couldn't load the billing settings." onRetry={() => page.refetch()} />;
  if (!page.data) return <Bar className="h-96 w-full rounded-card" />;
  return (
    <>
      <SellerPanel seller={page.data.seller} />
      <BankAccounts page={page.data} />
      <TaxRates page={page.data} />
      <Panel title="Ways to pay">
        <ul className="flex flex-col gap-1 text-body text-ink">
          {page.data.methods.map((m) => (
            <li key={m.key}>{m.label}<span className="text-muted">{m.needs_proof ? ": the customer sends proof, staff check it" : m.online ? ": paid in the app" : ": recorded by staff"}</span></li>
          ))}
        </ul>
        <p className="mt-2 text-meta text-muted">Set with BILLING_METHODS on the server; card payments also need the Stripe keys.</p>
      </Panel>
    </>
  );
}

function SellerPanel({ seller }: { seller: SellerSettings }) {
  const can = useStaff().can("billing.manage");
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState({ ...seller, tax_id: seller.tax_id ?? "", footer: seller.footer ?? "", reminder: seller.reminder_days.join(", ") });
  const set = (patch: Partial<typeof v>) => setV((x) => ({ ...x, ...patch }));
  return (
    <Panel title="Syruss's billing details" aside={seller.missing.length ? <Pill tone="negative">Not ready to invoice</Pill> : <Pill tone="positive">Ready</Pill>}>
      {!editing ? (
        <div className="flex flex-col gap-2 text-body">
          {seller.missing.length > 0 && <p className="text-negative">Customers can&apos;t buy until these are filled in: {seller.missing.join(", ")}.</p>}
          <p className="whitespace-pre-line text-ink"><b>{seller.legal_name || "—"}</b>{seller.address ? `\n${seller.address}` : ""}{seller.country ? `\n${seller.country}` : ""}</p>
          <p className="text-muted">Tax ID {seller.tax_id || "—"} · {seller.email || "no billing email"}</p>
          <p className="text-muted">Invoices numbered {seller.invoice_prefix}-YYYY-00001, due in {seller.payment_terms_days} days. Renewals go out {seller.renewal_lead_days} days
            before the period ends; reminders {seller.reminder_days.map((d) => (d > 0 ? `${d} days before` : d === 0 ? "on the day" : `${-d} days after`)).join(", ")};
            unpaid renewals move to Free {seller.grace_days} days after the due date.</p>
          {can && <div><Button size="sm" onClick={() => setEditing(true)}>Change</Button></div>}
        </div>
      ) : (
        <StaffForm title="Change Syruss's billing details" submit="Save" busy="Saving…" onDone={() => setEditing(false)}
          onRun={(reason) => api.billing.saveSettings({
            legal_name: v.legal_name, address: v.address, country: v.country, tax_id: v.tax_id || null, email: v.email, footer: v.footer || null,
            invoice_prefix: v.invoice_prefix, payment_terms_days: v.payment_terms_days, renewal_lead_days: v.renewal_lead_days, grace_days: v.grace_days,
            reminder_days: v.reminder.split(",").map((s) => Number(s.trim())).filter((n) => Number.isFinite(n)), reason })}>
          <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
            <Label text="Legal name"><input value={v.legal_name} onChange={(e) => set({ legal_name: e.target.value })} className={field} /></Label>
            <Label text="Billing email (slips to check are announced here)"><input type="email" value={v.email} onChange={(e) => set({ email: e.target.value })} className={field} /></Label>
            <Label text="Address (as printed)" className="@2xl:col-span-2">
              <textarea value={v.address} onChange={(e) => set({ address: e.target.value })} rows={3} className={`${field} h-auto py-1.5`} />
            </Label>
            <Label text="Country (2 letters)"><input value={v.country} maxLength={2} onChange={(e) => set({ country: e.target.value.toUpperCase() })} className={field} /></Label>
            <Label text="Tax registration"><input value={v.tax_id} onChange={(e) => set({ tax_id: e.target.value })} className={field} /></Label>
            <Label text="Invoice number prefix"><input value={v.invoice_prefix} onChange={(e) => set({ invoice_prefix: e.target.value.toUpperCase() })} className={field} /></Label>
            <Label text="Payment terms (days)"><input type="number" min={0} value={v.payment_terms_days} onChange={(e) => set({ payment_terms_days: Number(e.target.value) })} className={field} /></Label>
            <Label text="Renewal invoices go out (days before)"><input type="number" min={1} value={v.renewal_lead_days} onChange={(e) => set({ renewal_lead_days: Number(e.target.value) })} className={field} /></Label>
            <Label text="Grace period before Free (days)"><input type="number" min={0} value={v.grace_days} onChange={(e) => set({ grace_days: Number(e.target.value) })} className={field} /></Label>
            <Label text="Reminders (days before the due date; negative after)"><input value={v.reminder} onChange={(e) => set({ reminder: e.target.value })} className={field} placeholder="3, 0, -3" /></Label>
            <Label text="Footer printed on invoices (optional)"><input value={v.footer} onChange={(e) => set({ footer: e.target.value })} className={field} /></Label>
          </div>
        </StaffForm>
      )}
    </Panel>
  );
}

function BankAccounts({ page }: { page: BillingSettingsPage }) {
  const can = useStaff().can("billing.manage");
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [a, setA] = useState({ bank: "", account_name: "", account_number: "", branch: "", swift: "", currency: "USD", instructions: "" });
  const set = (patch: Partial<typeof a>) => setA((x) => ({ ...x, ...patch }));
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["billing", "settings"] });
  return (
    <Panel title="Bank accounts" aside={can && !adding && <Button size="sm" onClick={() => setAdding(true)}>Add an account</Button>}>
      {adding && (
        <div className="mb-3 rounded-control bg-raised p-3">
          <StaffForm title="An account customers pay into" submit="Add it" busy="Adding…" onDone={() => setAdding(false)}
            ready={!!(a.bank && a.account_name && a.account_number && /^[A-Z]{3}$/.test(a.currency))}
            onRun={(reason) => api.billing.addBankAccount({ ...a, branch: a.branch || null, swift: a.swift || null, instructions: a.instructions || null, reason })}>
            <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-3">
              <Label text="Bank"><input value={a.bank} onChange={(e) => set({ bank: e.target.value })} className={field} /></Label>
              <Label text="Account name"><input value={a.account_name} onChange={(e) => set({ account_name: e.target.value })} className={field} /></Label>
              <Label text="Account number"><input value={a.account_number} onChange={(e) => set({ account_number: e.target.value })} className={field} /></Label>
              <Label text="Branch"><input value={a.branch} onChange={(e) => set({ branch: e.target.value })} className={field} /></Label>
              <Label text="SWIFT"><input value={a.swift} onChange={(e) => set({ swift: e.target.value.toUpperCase() })} className={field} /></Label>
              <Label text="Currency (invoices in it show this account)"><input value={a.currency} maxLength={3} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} className={field} /></Label>
            </div>
            <Label text="Instructions for the customer (optional)"><input value={a.instructions} onChange={(e) => set({ instructions: e.target.value })} className={field} /></Label>
          </StaffForm>
        </div>
      )}
      {page.bank_accounts.length === 0 ? <p className="text-body text-muted">None yet: invoices can&apos;t tell customers where to pay by transfer.</p> : (
        <ul className="flex flex-col divide-y divide-rule">
          {page.bank_accounts.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center gap-2 py-2 text-body">
              <span className="min-w-0 flex-1">
                <span className="text-ink">{b.bank} · {b.account_name} · <span className="num">{b.account_number}</span></span>
                <span className="block text-meta text-muted">{b.currency}{b.branch ? ` · ${b.branch}` : ""}{b.swift ? ` · SWIFT ${b.swift}` : ""}</span>
              </span>
              <Pill tone={b.active ? "positive" : "neutral"}>{b.active ? "Shown on invoices" : "Not shown"}</Pill>
              {can && <ReasonAction label={b.active ? "Stop showing" : "Show again"} busyLabel="Saving…" confirmLabel="Save"
                onRun={(r) => api.billing.setBankAccountActive(b.id, !b.active, r).then(refresh)} />}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function TaxRates({ page }: { page: BillingSettingsPage }) {
  const can = useStaff().can("billing.manage");
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [t, setT] = useState({ country: "", name: "VAT", percent: "", reverse: false });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["billing", "settings"] });
  const bp = Math.round(Number(t.percent) * 100);
  return (
    <Panel title="Tax rates" aside={can && !adding && <Button size="sm" onClick={() => setAdding(true)}>Set a rate</Button>}>
      {adding && (
        <div className="mb-3 rounded-control bg-raised p-3">
          <StaffForm title="The tax on invoices to buyers in a country" submit="Set it" busy="Saving…" onDone={() => setAdding(false)}
            ready={/^([A-Z]{2}|\*)$/.test(t.country) && !!t.name && Number.isFinite(bp) && bp >= 0}
            onRun={(reason) => api.billing.addTaxRate({ country: t.country, name: t.name, rate_bp: bp, reverse_charge_with_tax_id: t.reverse, reason })}>
            <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-3">
              <Label text="Buyer's country (2 letters, or * for the rest)"><input value={t.country} maxLength={2} onChange={(e) => setT({ ...t, country: e.target.value.toUpperCase() })} className={field} /></Label>
              <Label text="Name as printed"><input value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} className={field} /></Label>
              <Label text="Rate (%)"><input inputMode="decimal" value={t.percent} onChange={(e) => setT({ ...t, percent: e.target.value })} className={field} placeholder="18" /></Label>
            </div>
            <label className="flex items-center gap-2 text-body text-ink">
              <input type="checkbox" checked={t.reverse} onChange={(e) => setT({ ...t, reverse: e.target.checked })} /> A business buyer with a tax ID accounts for it (reverse charge)
            </label>
          </StaffForm>
        </div>
      )}
      {page.tax_rates.length === 0 ? <p className="text-body text-muted">None: invoices carry no tax.</p> : (
        <ul className="flex flex-col divide-y divide-rule">
          {page.tax_rates.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2 py-2 text-body">
              <span className="min-w-0 flex-1 text-ink">{r.country === "*" ? "Every other country" : r.country}: {r.name} {r.rate_bp / 100}%
                {r.reverse_charge_with_tax_id && <span className="text-muted"> · reverse charge with a tax ID</span>}</span>
              <Pill tone={r.active ? "positive" : "neutral"}>{r.active ? "In use" : "Not in use"}</Pill>
              {can && <ReasonAction label={r.active ? "Stop using" : "Use again"} busyLabel="Saving…" confirmLabel="Save"
                onRun={(why) => api.billing.setTaxRateActive(r.id, !r.active, why).then(refresh)} />}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
