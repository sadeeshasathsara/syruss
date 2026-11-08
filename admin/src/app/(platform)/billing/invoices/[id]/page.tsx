"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { SlipFile } from "@/components/billing/PaymentsQueue";
import { Label, MoneyInput, StaffForm } from "@/components/billing/StaffForm";
import { Fact, ReasonAction, field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { KIND_OF_INVOICE, METHOD_LABEL, SUBMISSION_STATUS, day, invoiceStatus, money } from "@/lib/billing";
import { dateTime } from "@/lib/format";
import type { Invoice, Party } from "@/lib/types";

export default function InvoicePage() {
  const { id } = useParams<{ id: string }>();
  const invoice = useQuery({ queryKey: ["billing", "invoice", id], queryFn: () => api.billing.invoice(id) });
  const i = invoice.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader back={{ href: "/billing?tab=invoices", label: "← Invoices" }} title={i ? i.number ?? "Draft invoice" : "Invoice"}
        subtitle={i ? `${KIND_OF_INVOICE[i.kind]} · ${i.organization?.name ?? i.tenant_id}` : "Loading…"} />
      {invoice.isError && <ErrorNote message="Couldn't load this invoice." onRetry={() => invoice.refetch()} />}
      {!i ? <Bar className="h-96 w-full rounded-card" /> : <Body i={i} />}
    </div>
  );
}

function address(p: Party): string[] {
  if (p.address_line1) return [p.address_line1, p.address_line2, [p.postal_code, p.city].filter(Boolean).join(" "), p.region, p.country].filter(Boolean) as string[];
  return [...(p.address ?? "").split("\n"), p.country].filter(Boolean) as string[];
}

function PartyBlock({ label, p }: { label: string; p: Party }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 text-body">
      <span className="text-meta text-muted">{label}</span>
      <span className="font-medium text-ink">{p.legal_name ?? "—"}</span>
      {address(p).map((l, n) => <span key={n} className="text-ink">{l}</span>)}
      {p.tax_id && <span className="text-muted">Tax ID {p.tax_id}</span>}
      {p.email && <span className="text-muted">{p.email}</span>}
      {p.po_number && <span className="text-muted">PO {p.po_number}</span>}
    </div>
  );
}

function Body({ i }: { i: Invoice }) {
  const s = invoiceStatus(i);
  const cur = i.currency;
  // A draft can't be issued until both sides' billing details are in: the organization's (on the invoice) and Syruss's own.
  const settings = useQuery({ queryKey: ["billing", "settings"], queryFn: api.billing.settings, enabled: i.status === "draft" });
  const sellerMissing = settings.data?.seller.missing ?? [];
  return (
    <>
      <Panel>
        <dl className="grid grid-cols-2 gap-4 @3xl:grid-cols-4">
          <Fact label="State"><Pill tone={s.tone}>{s.label}</Pill>{i.switched_on && i.status === "open" && <Pill tone="accent" className="ml-1">Switched on</Pill>}</Fact>
          <Fact label="Total">{money(i.total_cents, cur)}</Fact>
          <Fact label="Still owed">{i.status === "draft" ? "— (not issued yet)" : money(i.amount_due_cents, cur)}</Fact>
          <Fact label="Issued · due">{day(i.issued_at)} · {day(i.due_at)}</Fact>
          <Fact label="Paid">{money(i.amount_paid_cents - i.amount_refunded_cents, cur)}</Fact>
          <Fact label="Credited">{money(i.amount_credited_cents, cur)}</Fact>
          <Fact label="Period">{i.period_start ? `${day(i.period_start)} to ${day(i.period_end)}` : "—"}</Fact>
          <Fact label="Files">
            {i.has_pdf ? <a className="hover:underline" href={api.billing.invoicePdfUrl(i.id)} target="_blank" rel="noreferrer">Invoice PDF</a> : "—"}
            {i.has_receipt && <> · <a className="hover:underline" href={api.billing.invoicePdfUrl(i.id, "receipt")} target="_blank" rel="noreferrer">Receipt</a></>}
          </Fact>
        </dl>
        {i.status === "void" && <p className="mt-3 text-body text-muted">Voided {day(i.voided_at)}: {i.void_reason}</p>}
        {i.switched_on_early_by && <p className="mt-3 text-body text-muted">Switched on before it was paid by {i.switched_on_early_by}: {i.switched_on_early_reason}</p>}
        {i.status === "draft" && !!i.billing_details_missing?.length && (
          <p className="mt-3 text-body text-negative">It can&apos;t be issued until the organization adds its billing details: {i.billing_details_missing.join(", ")}.</p>
        )}
        {i.status === "draft" && sellerMissing.length > 0 && (
          <p className="mt-3 text-body text-negative">
            Nor until Syruss&apos;s own billing details are filled in ({sellerMissing.join(", ")}): <Link href="/billing?tab=settings" className="underline">Billing settings</Link>.
          </p>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-6">
          <Panel title="Lines" bodyClassName="p-0 pt-2">
            <div className="overflow-x-auto scrollbar-thin">
              <table className="w-full min-w-[560px] text-body">
                <thead>
                  <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
                    <th scope="col">Description</th><th scope="col" className="text-right">Qty</th><th scope="col" className="text-right">Unit</th><th scope="col" className="text-right">Amount</th>
                  </tr>
                </thead>
                <tbody className="[&>tr]:border-b [&>tr]:border-rule [&_td]:px-4 [&_td]:py-2">
                  {(i.lines ?? []).map((l) => (
                    <tr key={l.id}>
                      <td className="text-ink">{l.description}{l.period_start && <span className="block text-meta text-muted">{day(l.period_start)} to {day(l.period_end)}</span>}</td>
                      <td className="num text-right">{l.quantity}</td>
                      <td className="num text-right">{money(l.unit_cents, cur)}</td>
                      <td className="num text-right text-ink">{money(l.amount_cents, cur)}</td>
                    </tr>
                  ))}
                  <tr><td colSpan={3} className="text-right text-muted">Subtotal</td><td className="num text-right">{money(i.subtotal_cents, cur)}</td></tr>
                  {i.tax_lines.map((t, n) => (
                    <tr key={n}><td colSpan={3} className="text-right text-muted">{t.name}{t.rate_bp ? ` (${t.rate_bp / 100}%)` : ""}{t.note && <span className="block text-meta">{t.note}</span>}</td>
                      <td className="num text-right">{money(t.tax_cents, cur)}</td></tr>
                  ))}
                  <tr className="border-0"><td colSpan={3} className="text-right font-medium text-ink">Total</td><td className="num text-right font-medium text-ink">{money(i.total_cents, cur)}</td></tr>
                </tbody>
              </table>
            </div>
            {i.note && <p className="px-4 pb-3 text-body text-muted">Note: {i.note}</p>}
          </Panel>
          {i.status !== "draft" && (
            <Panel><div className="grid grid-cols-1 gap-4 @2xl:grid-cols-2"><PartyBlock label="From" p={i.seller} /><PartyBlock label="Bill to" p={i.buyer} /></div></Panel>
          )}
          <Panel title="Payments">
            {!(i.payments ?? []).length ? <p className="text-body text-muted">None yet.</p> : (
              <ul className="flex flex-col divide-y divide-rule">
                {i.payments!.map((p) => (
                  <li key={p.id} className="flex flex-col gap-2 py-2.5 text-body">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="num font-medium text-ink">{money(p.amount_cents, p.currency)}</span>
                      <span className="min-w-0 flex-1 text-muted">{METHOD_LABEL[p.method] ?? p.method}{p.reference ? ` · ${p.reference}` : ""}{p.received_on ? ` · received ${day(p.received_on)}` : ""}</span>
                      <span className="text-meta text-muted">{p.recorded_by_name} · {dateTime(p.created_at)}</span>
                    </div>
                    {!!p.files?.length && <div className="grid grid-cols-1 gap-2 @2xl:grid-cols-2">{p.files.map((f) => <SlipFile key={f.id} url={api.billing.paymentFileUrl(p.id, f.id)} file={f} />)}</div>}
                    <AttachReceipt paymentId={p.id} />
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          {!!(i.submissions ?? []).length && (
            <Panel title="Slips the customer sent" aside={<Link href="/billing?tab=payments" className="text-meta hover:underline">Payments to check</Link>}>
              <ul className="flex flex-col divide-y divide-rule">
                {i.submissions!.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center gap-2 py-2 text-body">
                    <span className="num text-ink">{money(s.amount_cents, s.currency)}</span>
                    <span className="min-w-0 flex-1 text-muted">paid {day(s.paid_on)}{s.reference ? ` · ${s.reference}` : ""} · {s.files.length} file{s.files.length === 1 ? "" : "s"}
                      {s.rejection_reason ? ` · ${s.rejection_reason}` : ""}</span>
                    <Pill tone={SUBMISSION_STATUS[s.status].tone}>{SUBMISSION_STATUS[s.status].label}</Pill>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          {!!(i.credit_notes ?? []).length && (
            <Panel title="Credit notes">
              <ul className="flex flex-col divide-y divide-rule">
                {i.credit_notes!.map((n) => (
                  <li key={n.id} className="flex flex-wrap items-center gap-2 py-2 text-body">
                    <a className="font-medium text-ink hover:underline" href={api.billing.creditNotePdfUrl(n.id)} target="_blank" rel="noreferrer">{n.number}</a>
                    <span className="num text-ink">{money(n.amount_cents, n.currency)}</span>
                    <span className="min-w-0 flex-1 truncate text-muted">{n.reason}{n.refunds.length ? ` · refunded ${money(n.refunds.reduce((a, r) => a + r.amount_cents, 0), n.currency)}` : ""}</span>
                    <span className="text-meta text-muted">{n.created_by_name} · {day(n.created_at)}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
        <Actions i={i} />
      </div>
    </>
  );
}

function AttachReceipt({ paymentId }: { paymentId: string }) {
  const can = useStaff().can("billing.manage");
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  if (!can) return null;
  if (!open) return <div><Button size="sm" variant="ghost" onClick={() => setOpen(true)}>Attach a receipt…</Button></div>;
  return (
    <div className="rounded-control bg-raised p-3">
      <StaffForm title="Keep the receipt issued (or the cheque) with this payment" submit="Attach" busy="Uploading…" ready={files.length > 0}
        onDone={() => setOpen(false)} onRun={(reason) => api.billing.attachFiles(paymentId, files, reason)}>
        <input type="file" multiple accept="application/pdf,image/jpeg,image/png,image/webp" onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          className="text-body text-ink" aria-label="Receipt files" />
      </StaffForm>
    </div>
  );
}

type Doing = "payment" | "credit" | null;

function Actions({ i }: { i: Invoice }) {
  const me = useStaff();
  const router = useRouter();
  const queryClient = useQueryClient();
  const setup = useQuery({ queryKey: ["billing", "setup"], queryFn: api.billing.setup, staleTime: 300_000 });
  const [doing, setDoing] = useState<Doing>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["billing"] });
  if (!me.can("billing.manage")) return <Panel title="Changes"><p className="text-body text-muted">Your platform role sees billing; the Billing role changes it.</p></Panel>;
  const payable = i.status === "open" || i.status === "uncollectible";
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <Panel title="Changes">
        <div className="flex flex-col gap-3">
          {i.status === "draft" && (
            <>
              <ReasonAction label="Issue it" busyLabel="Issuing…" confirmLabel="Issue: it can't change after" onRun={(r) => api.billing.invoiceAction(i.id, "issue", r).then(refresh)} />
              <ReasonAction label="Throw the draft away" busyLabel="Discarding…" confirmLabel="Discard" variant="danger"
                onRun={(r) => api.billing.invoiceAction(i.id, "discard", r).then(() => router.push("/billing?tab=invoices"))} />
            </>
          )}
          {payable && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant={doing === "payment" ? "primary" : "secondary"} onClick={() => setDoing(doing === "payment" ? null : "payment")}>Record a payment</Button>
              {i.status === "open" && !i.switched_on && (
                <ReasonAction label="Switch on before it's paid" busyLabel="Switching on…" confirmLabel="Switch on" onRun={(r) => api.billing.invoiceAction(i.id, "switch-on", r).then(refresh)} />
              )}
              {i.amount_paid_cents === 0 && (
                <ReasonAction label="Void" busyLabel="Voiding…" confirmLabel="Void it" variant="danger" onRun={(r) => api.billing.invoiceAction(i.id, "void", r).then(refresh)} />
              )}
            </div>
          )}
          {i.status !== "draft" && i.status !== "void" && i.amount_credited_cents < i.total_cents && (
            <div><Button size="sm" variant={doing === "credit" ? "primary" : "secondary"} onClick={() => setDoing(doing === "credit" ? null : "credit")}>Credit note…</Button></div>
          )}
          {doing === "payment" && <PaymentForm i={i} methods={(setup.data?.methods ?? []).filter((m) => !m.online)} onDone={() => setDoing(null)} />}
          {doing === "credit" && <CreditForm i={i} onDone={() => setDoing(null)} />}
          {i.status === "paid" && !doing && <p className="text-body text-muted">Paid {day(i.paid_at)}. A correction is a credit note.</p>}
        </div>
      </Panel>
    </div>
  );
}

function PaymentForm({ i, methods, onDone }: { i: Invoice; methods: { key: string; label: string }[]; onDone: () => void }) {
  const [method, setMethod] = useState(methods.find((m) => m.key === "staff_recorded")?.key ?? methods[0]?.key ?? "");
  const [cents, setCents] = useState(i.amount_due_cents);
  const [receivedOn, setReceivedOn] = useState(new Date().toISOString().slice(0, 10));
  const [reference, setReference] = useState("");
  return (
    <div className="rounded-control bg-raised p-3">
      <StaffForm title="Money received for this invoice" submit={`Record ${money(cents, i.currency)}`} busy="Recording…" ready={!!method && cents > 0} onDone={onDone}
        onRun={(reason) => api.billing.recordPayment(i.id, { method, amount_cents: cents, received_on: receivedOn, reference: reference.trim() || undefined, reason })}>
        <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
          <Label text="How it was paid">
            <select value={method} onChange={(e) => setMethod(e.target.value)} className={field}>
              {methods.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
            </select>
          </Label>
          <Label text={`Amount (${i.currency})`}><MoneyInput cents={cents} onChange={setCents} /></Label>
          <Label text="Received on"><input type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} className={field} /></Label>
          <Label text="Reference (receipt, cheque or transfer number)"><input value={reference} onChange={(e) => setReference(e.target.value)} className={field} /></Label>
        </div>
      </StaffForm>
    </div>
  );
}

function CreditForm({ i, onDone }: { i: Invoice; onDone: () => void }) {
  const left = i.total_cents - i.amount_credited_cents;
  const [cents, setCents] = useState(Math.min(left, i.amount_due_cents || left));
  const [paymentId, setPaymentId] = useState("");
  const [reference, setReference] = useState("");
  const [why, setWhy] = useState("");
  const payment = (i.payments ?? []).find((p) => p.id === paymentId);
  return (
    <div className="rounded-control bg-raised p-3">
      <StaffForm title="Correct this invoice by an amount" submit={`Credit ${money(cents, i.currency)}`} busy="Issuing…" ready={cents > 0 && cents <= left && why.trim().length >= 5}
        onDone={onDone}
        onRun={(reason) => api.billing.creditNote(i.id, { amount_cents: cents, reason: why.trim() || reason, refund_payment_id: paymentId || undefined,
          refund_reference: reference.trim() || undefined })}>
        <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
          <Label text={`Amount (at most ${money(left, i.currency)})`}><MoneyInput cents={cents} onChange={setCents} /></Label>
          <Label text="Give it back from a payment (optional)">
            <select value={paymentId} onChange={(e) => setPaymentId(e.target.value)} className={field}>
              <option value="">No refund: take it off what is owed</option>
              {(i.payments ?? []).map((p) => <option key={p.id} value={p.id}>{money(p.amount_cents, p.currency)} · {METHOD_LABEL[p.method] ?? p.method}{p.reference ? ` · ${p.reference}` : ""}</option>)}
            </select>
          </Label>
          {payment && payment.method !== "card" && (
            <Label text="How it was given back (transfer or receipt number)"><input value={reference} onChange={(e) => setReference(e.target.value)} className={field} /></Label>
          )}
          {payment?.method === "card" && <p className="self-end text-meta text-muted">Refunded to the card through Stripe.</p>}
        </div>
        <Label text="What is corrected (printed on the credit note)"><input value={why} onChange={(e) => setWhy(e.target.value)} className={field} placeholder="Charged twice for the March pack" /></Label>
      </StaffForm>
    </div>
  );
}
