"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useStaff } from "@/components/shell/AdminFrame";
import { field } from "@/components/ops/Bits";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill, type Tone } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { Approval, ApprovalStatus } from "@/lib/types";

const STATUS: Record<ApprovalStatus, { label: string; tone: Tone }> = {
  pending: { label: "Waiting", tone: "warning" },
  expired: { label: "Expired", tone: "neutral" },
  done: { label: "Approved and done", tone: "positive" },
  rejected: { label: "Turned down", tone: "negative" },
  cancelled: { label: "Withdrawn", tone: "neutral" },
  failed: { label: "Couldn't be done", tone: "negative" },
};

/**
 * Risky actions held for a second owner (when the rule is on, under Platform controls). Any owner but the one who asked
 * approves or turns one down; the person who asked can withdraw it.
 */
export default function ApprovalsPage() {
  const [show, setShow] = useState<"pending" | "all">("pending");
  const list = useQuery({ queryKey: ["approvals", show], queryFn: () => api.approvals.list(show), refetchInterval: 30_000 });
  const rule = useQuery({ queryKey: ["approvals", "rule"], queryFn: api.approvals.rule });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Approvals"
        subtitle={rule.data?.on
          ? `Risky actions wait here for a second owner, for up to ${rule.data.hours} hours. They're carried out as the person who asked once another owner approves.`
          : "The second owner's approval is off: risky actions happen at once. Owners can turn it on under Platform controls."}
        actions={<Link href="/platform#approvals" className="text-body text-accent hover:underline">The rule</Link>}
      />
      <div className="flex gap-1" role="tablist" aria-label="Which approvals">
        {(["pending", "all"] as const).map((s) => (
          <Button key={s} size="sm" variant={show === s ? "primary" : "secondary"} role="tab" aria-selected={show === s} onClick={() => setShow(s)}>
            {s === "pending" ? "Waiting" : "Everything"}
          </Button>
        ))}
      </div>
      {list.isError ? <ErrorNote message={list.error.message} onRetry={() => list.refetch()} /> : !list.data ? (
        <Bar className="h-40 w-full" />
      ) : !list.data.items.length ? (
        <Empty title={show === "pending" ? "Nothing waiting" : "No approvals yet"}>
          {show === "pending" ? "No risky action is waiting for a second owner." : "Nothing has been held for approval."}
        </Empty>
      ) : (
        <Panel title={show === "pending" ? "Waiting for a second owner" : "All approvals"} bodyClassName="p-0">
          <ul className="flex flex-col divide-y divide-rule">{list.data.items.map((a) => <Row key={a.id} approval={a} />)}</ul>
        </Panel>
      )}
    </div>
  );
}

function Row({ approval: a }: { approval: Approval }) {
  const me = useStaff();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);
  const refresh = () => queryClient.invalidateQueries();
  const approve = useMutation({ mutationFn: () => api.approvals.approve(a.id, note.trim()), onSuccess: () => { setDeciding(null); refresh(); } });
  const reject = useMutation({ mutationFn: () => api.approvals.reject(a.id, note.trim()), onSuccess: () => { setDeciding(null); refresh(); } });
  const cancel = useMutation({ mutationFn: () => api.approvals.cancel(a.id), onSuccess: refresh });
  const mine = a.requested_by === me.user_id;
  const open = a.status === "pending";
  const mayDecide = open && !mine && me.role === "owner";
  const status = STATUS[a.status];
  const busy = approve.isPending || reject.isPending;

  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 text-body text-ink">
            <span className="font-medium">{a.summary}</span>
            <Pill tone={status.tone}>{status.label}</Pill>
          </p>
          <p className="text-meta text-muted">
            Asked by {mine ? "you" : a.requested_by_name} {ago(a.created_at).toLowerCase()}
            {open ? ` · expires ${new Date(a.expires_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}` : ""}
            {a.decided_by_name && !open ? ` · ${a.status === "cancelled" ? "withdrawn" : "decided"} by ${a.decided_by_name} ${ago(a.decided_at!).toLowerCase()}` : ""}
          </p>
          {a.reason && <p className="mt-1 text-body text-ink"><span className="text-muted">Why: </span>{a.reason}</p>}
          {a.note && <p className="mt-1 text-body text-ink"><span className="text-muted">{a.status === "failed" ? "What happened: " : "Note: "}</span>{a.note}</p>}
        </div>
        {mayDecide && !deciding && (
          <div className="flex gap-2">
            <Button size="sm" variant="primary" onClick={() => setDeciding("approve")}>Approve…</Button>
            <Button size="sm" variant="danger" onClick={() => setDeciding("reject")}>Turn down…</Button>
          </div>
        )}
        {open && mine && <Button size="sm" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Withdraw</Button>}
        {open && !mine && me.role !== "owner" && <span className="text-meta text-muted">An owner decides</span>}
      </div>
      {deciding && (
        <form className={cn("flex flex-col gap-2 rounded-control bg-raised p-3 @2xl:flex-row @2xl:items-center")}
              onSubmit={(e) => { e.preventDefault(); if (deciding === "approve") approve.mutate(); else if (note.trim()) reject.mutate(); }}>
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} required={deciding === "reject"} autoFocus
                 aria-label={deciding === "approve" ? "Note (optional)" : "Why it's turned down"}
                 placeholder={deciding === "approve" ? "Note for the audit log (optional)" : `Why (${a.requested_by_name} is told)`} className={cn(field, "min-w-0 flex-1")} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant={deciding === "approve" ? "primary" : "danger"} disabled={busy || (deciding === "reject" && !note.trim())}>
              {deciding === "approve" ? (approve.isPending ? "Carrying it out…" : "Approve and carry it out") : (reject.isPending ? "Turning down…" : "Turn it down")}
            </Button>
            <Button size="sm" onClick={() => setDeciding(null)}>Cancel</Button>
          </div>
        </form>
      )}
      <Problem error={approve.error ?? reject.error ?? cancel.error} />
    </li>
  );
}
