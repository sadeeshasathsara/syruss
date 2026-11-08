"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Label, MoneyInput, StaffForm } from "@/components/billing/StaffForm";
import { field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, Empty, ErrorNote, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { ApiError, api } from "@/lib/api";
import { METHOD_LABEL, SUBMISSION_STATUS, day, money } from "@/lib/billing";
import { dateTime } from "@/lib/format";
import type { PaymentFileInfo, Submission } from "@/lib/types";

/** Payments customers sent proof of: the slip beside what the invoice asks for, to approve for what arrived or reject. */
export function PaymentsQueue() {
  const [status, setStatus] = useState<Submission["status"]>("submitted");
  const list = useQuery({ queryKey: ["billing", "submissions", status], queryFn: () => api.billing.submissions(status) });
  return (
    <>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Which payments">
        {(["submitted", "approved", "rejected", "withdrawn"] as const).map((s) => (
          <Button key={s} size="sm" variant={status === s ? "primary" : "secondary"} onClick={() => setStatus(s)}>{SUBMISSION_STATUS[s].label}</Button>
        ))}
      </div>
      {list.isError && <ErrorNote message="Couldn't load the payments." onRetry={() => list.refetch()} />}
      {!list.data ? <Bar className="h-48 w-full rounded-card" /> : list.data.length === 0 ? (
        <Empty title={status === "submitted" ? "Nothing to check" : "None"}>
          {status === "submitted" ? "When a customer sends the slip for a bank transfer, it shows here to check against the account." : undefined}
        </Empty>
      ) : (
        <div className="flex flex-col gap-3">{list.data.map((s) => <SlipCard key={s.id} s={s} />)}</div>
      )}
    </>
  );
}

/** A photo of the slip is shown here; a PDF is downloaded to open (it is never run in the admin app). */
export function SlipFile({ url, file }: { url: string; file: PaymentFileInfo }) {
  return file.content_type.startsWith("image/") ? (
    <a href={url} target="_blank" rel="noreferrer" className="block">
      <img src={url} alt={file.filename} className="max-h-72 w-full rounded-control border border-border bg-sheet object-contain" />
    </a>
  ) : (
    <a href={url} className="flex items-center justify-between gap-2 rounded-control border border-border bg-sheet px-3 py-2.5 text-body text-ink hover:bg-raised">
      <span className="min-w-0 truncate">{file.filename}</span>
      <span className="flex-none text-meta text-muted">PDF · {Math.max(1, Math.round(file.size / 1024))} KB · Download</span>
    </a>
  );
}

function SlipCard({ s }: { s: Submission }) {
  const me = useStaff();
  const [doing, setDoing] = useState<"approve" | "reject" | null>(null);
  const [cents, setCents] = useState(s.amount_cents);
  const [receivedOn, setReceivedOn] = useState(s.paid_on);
  const [overpaid, setOverpaid] = useState(false);
  const [askOverpaid, setAskOverpaid] = useState(false);
  const invoice = s.invoice;
  const status = SUBMISSION_STATUS[s.status];
  return (
    <Panel>
      <div className="grid grid-cols-1 gap-4 @3xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/billing/${s.tenant_id}`} className="font-medium text-ink hover:underline">{s.organization?.name ?? s.tenant_id}</Link>
            <Pill tone={status.tone}>{status.label}</Pill>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-body">
            <dt className="text-muted">Sent</dt><dd className="num font-medium text-ink">{money(s.amount_cents, s.currency)}</dd>
            {invoice && <><dt className="text-muted">Invoice</dt><dd><Link href={`/billing/invoices/${invoice.id}`} className="text-ink hover:underline">{invoice.number}</Link></dd></>}
            {invoice && <><dt className="text-muted">Due on it</dt><dd className="num text-ink">{money(invoice.amount_due_cents, invoice.currency)} of {money(invoice.total_cents, invoice.currency)}</dd></>}
            <dt className="text-muted">Paid by</dt><dd className="text-ink">{METHOD_LABEL[s.method] ?? s.method}, on {day(s.paid_on)}</dd>
            <dt className="text-muted">Bank reference</dt><dd className="text-ink">{s.reference ?? "—"}</dd>
            <dt className="text-muted">Sent by</dt><dd className="text-ink">{s.submitted_by_name} · {dateTime(s.created_at)}</dd>
          </dl>
          {s.note && <p className="text-body text-ink">“{s.note}”</p>}
          {!!s.warnings?.length && (
            <ul className="flex flex-col gap-1 rounded-control bg-[var(--pill-warning-bg)] px-3 py-2 text-body text-[var(--pill-warning-ink)]">
              {s.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          {s.status !== "submitted" && s.reviewed_by_name && (
            <p className="text-meta text-muted">{status.label} by {s.reviewed_by_name} · {dateTime(s.reviewed_at!)}{s.rejection_reason ? `: ${s.rejection_reason}` : ""}</p>
          )}
          {s.status === "submitted" && me.can("billing.manage") && !doing && (
            <div className="flex gap-2">
              <Button size="sm" variant="primary" onClick={() => setDoing("approve")}>Approve…</Button>
              <Button size="sm" onClick={() => setDoing("reject")}>Reject…</Button>
            </div>
          )}
          {doing === "approve" && (
            <div className="rounded-control bg-raised p-3">
              <StaffForm title="The money is on the account" submit={`Approve ${money(cents, s.currency)}`} busy="Approving…" onDone={() => setDoing(null)}
                onRun={async (reason) => {
                  try {
                    return await api.billing.approve(s.id, { amount_cents: cents, received_on: receivedOn, overpaid_ok: overpaid, reason });
                  } catch (e) {
                    if (e instanceof ApiError && e.code === "overpayment") setAskOverpaid(true);
                    throw e;
                  }
                }}>
                <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
                  <Label text={`What arrived (${s.currency})`}><MoneyInput cents={cents} onChange={setCents} /></Label>
                  <Label text="Arrived on"><input type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} className={field} /></Label>
                </div>
                {askOverpaid && (
                  <label className="flex items-center gap-2 text-body text-ink">
                    <input type="checkbox" checked={overpaid} onChange={(e) => setOverpaid(e.target.checked)} /> Record it although it is more than is due
                  </label>
                )}
              </StaffForm>
            </div>
          )}
          {doing === "reject" && (
            <div className="rounded-control bg-raised p-3">
              <StaffForm title="Reject it: the customer sees why and can send it again" submit="Reject" busy="Rejecting…" danger onDone={() => setDoing(null)}
                onRun={(reason) => api.billing.reject(s.id, reason)} />
            </div>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-3">
          {s.files.length === 0 ? <p className="text-body text-muted">No file.</p>
            : s.files.map((f) => <SlipFile key={f.id} url={api.billing.submissionFileUrl(s.id, f.id)} file={f} />)}
        </div>
      </div>
    </Panel>
  );
}
