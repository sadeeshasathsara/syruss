"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, useState } from "react";
import { Gate, Loading, money, useBilling } from "@/components/org/billing";
import { Section, SettingsHeader } from "@/components/settings/kit";
import { ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { BillingDetails, Invoice, SentPayment } from "@/lib/types";

const day = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "—");
const METHOD: Record<string, string> = { bank_transfer: "Bank transfer", staff_recorded: "Recorded by Syruss", card: "Card" };

function state(i: Pick<Invoice, "status" | "overdue">): { label: string; tone: "positive" | "warning" | "negative" | "neutral" } {
  if (i.status === "paid") return { label: "Paid", tone: "positive" };
  if (i.status === "void") return { label: "Void", tone: "neutral" };
  if (i.status === "uncollectible") return { label: "Not paid", tone: "negative" };
  return i.overdue ? { label: "Overdue", tone: "negative" } : { label: "Waiting for payment", tone: "warning" };
}

/** "49.00" typed, kept as cents. */
function MoneyField({ cents, onChange, id }: { cents: number; onChange: (c: number) => void; id: string }) {
  const [text, setText] = useState((cents / 100).toFixed(2));
  return <Input id={id} inputMode="decimal" value={text} onChange={(e) => {
    setText(e.target.value);
    const v = Number(e.target.value);
    if (Number.isFinite(v) && v >= 0) onChange(Math.round(v * 100));
  }} />;
}

// ---- Invoices ---------------------------------------------------------------------------------

export function BillingInvoices() {
  const { allowed, role } = useBilling();
  const open = useSearchParams().get("invoice");
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Invoices" description="Every invoice issued to the organization: download it, pay it, and get the receipt once it's paid." />
      <Gate allowed={allowed} role={role}>{open ? <InvoiceView id={open} /> : <InvoiceList />}</Gate>
    </div>
  );
}

function InvoiceList() {
  const list = useQuery({ queryKey: ["org-invoices"], queryFn: api.billing.invoices });
  if (list.isError) return <div className="pt-6"><ErrorNote message="Couldn't load the invoices." onRetry={() => list.refetch()} /></div>;
  if (!list.data) return <Loading />;
  if (!list.data.length) return <p className="pt-6 text-body text-muted">No invoices yet. Buying a plan, an add-on or credits issues one.</p>;
  return (
    <ul className="mt-6 flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
      {list.data.map((i) => {
        const s = state(i);
        return (
          <li key={i.id}>
            <Link href={`?invoice=${i.id}`} className="flex flex-wrap items-center gap-3 px-4 py-3 text-body hover:bg-raised">
              <span className="font-medium text-ink">{i.number}</span>
              <span className="min-w-0 flex-1 text-muted">Issued {day(i.issued_at)} · due {day(i.due_at)}</span>
              <span className="num text-ink">{money(i.total_cents, i.currency)}</span>
              <Pill tone={s.tone}>{s.label}</Pill>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function InvoiceView({ id }: { id: string }) {
  const params = useSearchParams();
  const invoice = useQuery({ queryKey: ["org-invoice", id], queryFn: () => api.billing.invoice(id), refetchInterval: params.get("paid") === "card" ? 4000 : false });
  const i = invoice.data;
  if (invoice.isError) return <div className="pt-6"><ErrorNote message="Couldn't load this invoice." onRetry={() => invoice.refetch()} /></div>;
  if (!i) return <Loading />;
  const s = state(i);
  const payable = i.status === "open" || i.status === "uncollectible";
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 pt-6">
        <Link href="?" className="text-body text-muted hover:text-ink">← All invoices</Link>
      </div>
      {params.get("paid") === "card" && i.status !== "paid" && (
        <p className="mt-4 rounded-card bg-[var(--pill-info-bg)] px-4 py-3 text-body text-[var(--pill-info-ink)]">Thank you: your card payment is being confirmed. This page updates by itself.</p>
      )}
      <Section title={`Invoice ${i.number}`} description={<span className="flex flex-col gap-1"><Pill tone={s.tone}>{s.label}</Pill>
        <span>Issued {day(i.issued_at)}, due {day(i.due_at)}.</span>
        {i.status === "uncollectible" && <span>The organization moved to the Free plan; paying this puts its plan back.</span>}</span>}>
        <div className="flex flex-col gap-4 rounded-card border border-border bg-card p-4">
          <ul className="flex flex-col divide-y divide-rule text-body">
            {(i.lines ?? []).map((l) => (
              <li key={l.id} className="flex items-baseline justify-between gap-3 py-2">
                <span className="min-w-0 text-ink">{l.quantity > 1 ? `${l.quantity} × ` : ""}{l.description}
                  {l.period_start && <span className="block text-meta text-muted">{day(l.period_start)} to {day(l.period_end)}</span>}</span>
                <span className="num text-ink">{money(l.amount_cents, i.currency)}</span>
              </li>
            ))}
          </ul>
          <dl className="ml-auto grid w-full max-w-xs grid-cols-2 gap-x-4 gap-y-1 text-body">
            <dt className="text-muted">Subtotal</dt><dd className="num text-right">{money(i.subtotal_cents, i.currency)}</dd>
            {i.tax_lines.map((t, n) => (
              <Fragment key={n}><dt className="text-muted">{t.name}{t.rate_bp ? ` ${t.rate_bp / 100}%` : ""}</dt><dd className="num text-right">{money(t.tax_cents, i.currency)}</dd></Fragment>
            ))}
            <dt className="font-medium text-ink">Total</dt><dd className="num text-right font-medium text-ink">{money(i.total_cents, i.currency)}</dd>
            {i.amount_paid_cents > 0 && <><dt className="text-muted">Paid</dt><dd className="num text-right">{money(i.amount_paid_cents - i.amount_refunded_cents, i.currency)}</dd></>}
            {i.amount_credited_cents > 0 && <><dt className="text-muted">Credited</dt><dd className="num text-right">{money(i.amount_credited_cents, i.currency)}</dd></>}
            {payable && <><dt className="font-medium text-ink">Still owed</dt><dd className="num text-right font-medium text-ink">{money(i.amount_due_cents, i.currency)}</dd></>}
          </dl>
          {i.tax_lines.filter((t) => t.note).map((t, n) => <p key={n} className="text-meta text-muted">{t.note}</p>)}
          <p className="flex flex-wrap gap-3 text-body">
            {i.has_pdf && <a className="text-ink underline" href={api.billing.fileUrl(`invoices/${i.id}/pdf`)} target="_blank" rel="noreferrer">Download the invoice (PDF)</a>}
            {i.has_receipt && <a className="text-ink underline" href={api.billing.fileUrl(`invoices/${i.id}/receipt`)} target="_blank" rel="noreferrer">Download the receipt (PDF)</a>}
          </p>
        </div>
      </Section>
      {payable && <HowToPay i={i} />}
      {!!i.submissions?.length && <SentPayments i={i} />}
      {!!i.payments?.length && (
        <Section title="Payments received">
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
            {i.payments.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-body">
                <span className="num font-medium text-ink">{money(p.amount_cents, p.currency)}</span>
                <span className="min-w-0 flex-1 text-muted">{METHOD[p.method] ?? p.method}{p.reference ? ` · ${p.reference}` : ""}</span>
                <span className="text-meta text-muted">{day(p.received_on ?? p.created_at)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {!!i.credit_notes?.length && (
        <Section title="Credit notes" description="Corrections to this invoice.">
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
            {i.credit_notes.map((n) => (
              <li key={n.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-body">
                <a className="font-medium text-ink underline" href={api.billing.fileUrl(`credit-notes/${n.id}/pdf`)} target="_blank" rel="noreferrer">{n.number}</a>
                <span className="num text-ink">{money(n.amount_cents, n.currency)}</span>
                <span className="min-w-0 flex-1 truncate text-muted">{n.reason}{n.refunds.length ? ` · refunded ${money(n.refunds.reduce((a, r) => a + r.amount_cents, 0), n.currency)}` : ""}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

function HowToPay({ i }: { i: Invoice }) {
  const waiting = (i.submissions ?? []).some((s) => s.status === "submitted");
  return (
    <Section title="How to pay" description={`${money(i.amount_due_cents, i.currency)} is still owed. What it's for is switched on as soon as the payment is confirmed.`}>
      <div className="flex flex-col gap-4">
        {(i.how_to_pay ?? []).map((m) => m.key === "bank_transfer" ? (
          <div key={m.key} className="flex flex-col gap-3 rounded-card border border-border bg-card p-4">
            <p className="text-body font-semibold text-ink">Bank transfer</p>
            {!m.instructions?.accounts?.length ? <p className="text-body text-muted">Ask Syruss for the account details for {i.currency}.</p> : (
              <>
                <p className="text-body text-ink">Pay into this account, quoting <b className="num">{m.instructions.reference}</b> as the reference, then send the slip below.</p>
                {m.instructions.accounts.map((a, n) => (
                  <dl key={n} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-control bg-raised px-3 py-2 text-body">
                    <dt className="text-muted">Bank</dt><dd className="text-ink">{a.bank}{a.branch ? `, ${a.branch}` : ""}</dd>
                    <dt className="text-muted">Account name</dt><dd className="text-ink">{a.account_name}</dd>
                    <dt className="text-muted">Account number</dt><dd className="num text-ink">{a.account_number}</dd>
                    {a.swift && <><dt className="text-muted">SWIFT</dt><dd className="num text-ink">{a.swift}</dd></>}
                    {a.instructions && <><dt className="text-muted">Note</dt><dd className="text-ink">{a.instructions}</dd></>}
                  </dl>
                ))}
              </>
            )}
            {waiting ? <p className="text-body text-muted">The slip you sent is being checked. You can withdraw it below to send a corrected one.</p> : <SendSlip i={i} />}
          </div>
        ) : m.key === "card" ? <PayByCard key={m.key} i={i} /> : null)}
        {!(i.how_to_pay ?? []).length && <p className="text-body text-muted">Contact Syruss to pay this invoice.</p>}
      </div>
    </Section>
  );
}

function SendSlip({ i }: { i: Invoice }) {
  const queryClient = useQueryClient();
  const [cents, setCents] = useState(i.amount_due_cents);
  const [paidOn, setPaidOn] = useState(new Date().toISOString().slice(0, 10));
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const send = useMutation({
    mutationFn: () => api.billing.sendPayment(i.id, { method: "bank_transfer", amount_cents: cents, paid_on: paidOn, reference: reference.trim() || undefined,
      note: note.trim() || undefined, files }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-invoice", i.id] });
      queryClient.invalidateQueries({ queryKey: ["org-billing"] });
    },
  });
  if (send.isSuccess) return <p className="text-body text-positive">Sent. Syruss checks it against the account and tells you once it&apos;s confirmed.</p>;
  return (
    <form className="flex flex-col gap-3 border-t border-border pt-3" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
      <p className="text-body font-medium text-ink">Send the slip</p>
      <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-3">
        <Field label={`Amount paid (${i.currency})`}>{(id) => <MoneyField id={id} cents={cents} onChange={setCents} />}</Field>
        <Field label="Paid on">{(id) => <Input id={id} type="date" value={paidOn} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setPaidOn(e.target.value)} />}</Field>
        <Field label="Bank reference (optional)">{(id) => <Input id={id} value={reference} maxLength={200} onChange={(e) => setReference(e.target.value)} />}</Field>
      </div>
      <Field label="The slip or transfer confirmation" help="A PDF or a photo (JPEG, PNG or WebP), up to 10 MB each, up to 5 files.">
        {(id) => <input id={id} type="file" multiple accept="application/pdf,image/jpeg,image/png,image/webp" className="text-body text-ink"
          onChange={(e) => setFiles(Array.from(e.target.files ?? []).slice(0, 5))} />}
      </Field>
      <Field label="Note for Syruss (optional)">{(id) => <Input id={id} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />}</Field>
      {send.error && <p className="text-body text-negative">{send.error.message}</p>}
      <div><Button type="submit" variant="primary" disabled={send.isPending || !files.length || cents <= 0}>{send.isPending ? "Sending…" : "Send it to Syruss"}</Button></div>
    </form>
  );
}

function PayByCard({ i }: { i: Invoice }) {
  const [keep, setKeep] = useState(i.kind === "renewal" || i.lines?.some((l) => l.description.endsWith("plan, one month")) || false);
  const pay = useMutation({ mutationFn: () => api.billing.checkout(i.id, keep), onSuccess: (r) => window.location.assign(r.url) });
  return (
    <div className="flex flex-col gap-3 rounded-card border border-border bg-card p-4">
      <p className="text-body font-semibold text-ink">Card</p>
      <p className="text-body text-muted">Pay on Stripe&apos;s secure page; your card details never reach Syruss.</p>
      <label className="flex items-center gap-2 text-body text-ink"><input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} /> Keep the card to pay renewals by themselves</label>
      {pay.error && <p className="text-body text-negative">{pay.error.message}</p>}
      <div><Button variant="primary" disabled={pay.isPending} onClick={() => pay.mutate()}>{pay.isPending ? "Opening…" : `Pay ${money(i.amount_due_cents, i.currency)} by card`}</Button></div>
    </div>
  );
}

const SENT: Record<SentPayment["status"], { label: string; tone: "positive" | "warning" | "negative" | "neutral" }> = {
  submitted: { label: "Being checked", tone: "warning" }, approved: { label: "Confirmed", tone: "positive" },
  rejected: { label: "Not confirmed", tone: "negative" }, withdrawn: { label: "Withdrawn", tone: "neutral" },
};

function SentPayments({ i }: { i: Invoice }) {
  const queryClient = useQueryClient();
  const withdraw = useMutation({
    mutationFn: (id: string) => api.billing.withdraw(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["org-invoice", i.id] }),
  });
  return (
    <Section title="Slips you sent">
      <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
        {i.submissions!.map((s) => (
          <li key={s.id} className="flex flex-col gap-1 px-4 py-3 text-body">
            <div className="flex flex-wrap items-center gap-3">
              <span className="num font-medium text-ink">{money(s.amount_cents, s.currency)}</span>
              <span className="min-w-0 flex-1 text-muted">paid {day(s.paid_on)}{s.reference ? ` · ${s.reference}` : ""} · sent by {s.submitted_by_name}, {dateTime(s.created_at)}</span>
              <Pill tone={SENT[s.status].tone}>{SENT[s.status].label}</Pill>
              {s.status === "submitted" && <Button size="sm" variant="secondary" disabled={withdraw.isPending} onClick={() => withdraw.mutate(s.id)}>Withdraw</Button>}
            </div>
            {s.rejection_reason && <p className="text-body text-negative">Syruss said: {s.rejection_reason}</p>}
            <p className="flex flex-wrap gap-3 text-meta">
              {s.files.map((f) => <a key={f.id} className="text-muted underline" href={api.billing.fileUrl(`submissions/${s.id}/files/${f.id}`)} target="_blank" rel="noreferrer">{f.filename}</a>)}
            </p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

// ---- Billing details --------------------------------------------------------------------------

const EMPTY: BillingDetails = { legal_name: "", email: "", address_line1: "", address_line2: null, city: "", region: null, postal_code: null, country: "",
  tax_id: null, po_number: null };

export function BillingDetailsPage() {
  const { allowed, role, billing } = useBilling();
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Billing details" description="Who invoices are made out to. Invoices already issued keep the details they were issued with." />
      <Gate allowed={allowed} role={role}>
        <DetailsForm />
        {billing.data && <KeptCard card={billing.data.card} />}
      </Gate>
    </div>
  );
}

function DetailsForm() {
  const current = useQuery({ queryKey: ["org-billing-details"], queryFn: api.billing.details });
  if (current.isError) return <div className="pt-6"><ErrorNote message="Couldn't load the billing details." onRetry={() => current.refetch()} /></div>;
  if (!current.data) return <Loading />;
  return <DetailsEditor initial={current.data.profile ?? EMPTY} missing={current.data.missing} />;
}

function DetailsEditor({ initial, missing }: { initial: BillingDetails; missing: string[] }) {
  const queryClient = useQueryClient();
  const [v, setV] = useState<BillingDetails>(initial);
  const save = useMutation({
    mutationFn: () => api.billing.saveDetails(v),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-billing-details"] });
      queryClient.invalidateQueries({ queryKey: ["org-billing"] });
    },
  });
  const set = (k: keyof BillingDetails) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.target.value || (EMPTY[k] === null ? null : "") });
  const stillMissing = save.data ? save.data.missing : missing;
  return (
    <Section title="Made out to" description={stillMissing.length ? `Needed before an invoice can be issued: ${stillMissing.join(", ")}.` : "Complete."}>
      <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-2">
          <Field label="Legal name">{(id) => <Input id={id} required value={v.legal_name} onChange={set("legal_name")} />}</Field>
          <Field label="Billing email" help="Invoices, receipts and reminders are sent here.">{(id) => <Input id={id} type="email" required value={v.email} onChange={set("email")} />}</Field>
          <Field label="Address">{(id) => <Input id={id} required value={v.address_line1} onChange={set("address_line1")} />}</Field>
          <Field label="Address, second line (optional)">{(id) => <Input id={id} value={v.address_line2 ?? ""} onChange={set("address_line2")} />}</Field>
          <Field label="City">{(id) => <Input id={id} required value={v.city} onChange={set("city")} />}</Field>
          <Field label="Postal code (optional)">{(id) => <Input id={id} value={v.postal_code ?? ""} onChange={set("postal_code")} />}</Field>
          <Field label="Region (optional)">{(id) => <Input id={id} value={v.region ?? ""} onChange={set("region")} />}</Field>
          <Field label="Country" help="Two letters, such as LK, GB or US. It decides the tax.">
            {(id) => <Input id={id} required maxLength={2} value={v.country} onChange={(e) => setV({ ...v, country: e.target.value.toUpperCase() })} />}
          </Field>
          <Field label="Tax ID (optional)">{(id) => <Input id={id} value={v.tax_id ?? ""} onChange={set("tax_id")} />}</Field>
          <Field label="Purchase order number (optional)" help="Printed on every invoice.">{(id) => <Input id={id} value={v.po_number ?? ""} onChange={set("po_number")} />}</Field>
        </div>
        {save.error && <p className="text-body text-negative">{save.error.message}</p>}
        {save.isSuccess && <p className="text-body text-positive">Saved.</p>}
        <div><Button type="submit" variant="primary" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button></div>
      </form>
    </Section>
  );
}

function KeptCard({ card }: { card: { kept: boolean; label: string | null; can_manage: boolean } }) {
  const queryClient = useQueryClient();
  const portal = useMutation({ mutationFn: api.billing.cardPortal, onSuccess: (r) => window.location.assign(r.url) });
  const forget = useMutation({ mutationFn: api.billing.forgetCard, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["org-billing"] }) });
  if (!card.kept && !card.can_manage) return null;
  return (
    <Section title="Card for renewals" description="Renewal invoices are paid with it by themselves; if it's declined, you're told and can pay by hand.">
      <div className="flex flex-wrap items-center gap-3 rounded-card border border-border bg-card p-4 text-body">
        <span className="min-w-0 flex-1 text-ink">{card.kept ? card.label ?? "A card is kept" : "No card is kept: renewals are invoiced to pay by hand."}</span>
        {card.can_manage && <Button variant="secondary" disabled={portal.isPending} onClick={() => portal.mutate()}>Change the card</Button>}
        {card.kept && <Button variant="secondary" disabled={forget.isPending} onClick={() => forget.mutate()}>Stop using it</Button>}
      </div>
      {(portal.error || forget.error) && <p className="mt-2 text-body text-negative">{(portal.error ?? forget.error)?.message}</p>}
    </Section>
  );
}
