"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, LockKeyhole } from "lucide-react";
import { useState } from "react";
import { useStaff } from "@/components/shell/AdminFrame";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { LiveState } from "@/lib/live";
import type { Grant } from "@/lib/types";

export const field = "h-8 rounded-control border border-control bg-sheet px-2.5 text-body text-ink outline-none focus-visible:border-accent disabled:opacity-60";

const STATE: Record<LiveState, { dot: string; label: string }> = {
  live: { dot: "bg-positive animate-pulse", label: "Live" },
  connecting: { dot: "bg-[var(--pill-warning-ink)]", label: "Connecting…" },
  paused: { dot: "bg-faint", label: "Paused" },
  closed: { dot: "bg-negative", label: "Stopped: your access changed" },
};

/** Whether the page is following the platform live. */
export function LiveDot({ state, missed }: { state: LiveState; missed?: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-meta text-muted" role="status">
      <span aria-hidden className={cn("size-2 rounded-full", STATE[state].dot)} />
      {STATE[state].label}
      {!!missed && <span className="text-[var(--pill-warning-ink)]"> · {missed} event{missed === 1 ? "" : "s"} skipped while busy</span>}
    </span>
  );
}

const until = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/**
 * Facts, or an organization's content: the banner every detail page carries. Content opens only for a reason and a
 * time, and the organization sees in its own activity that Syruss support looked.
 */
export function ContentAccess({ tenantId, organization, grant }: { tenantId: string; organization: string; grant: Grant | null }) {
  const me = useStaff();
  const queryClient = useQueryClient();
  const [opening, setOpening] = useState(false);
  const [reason, setReason] = useState("");
  const [minutes, setMinutes] = useState(60);
  const refresh = () => queryClient.invalidateQueries();
  const open = useMutation({ mutationFn: () => api.access.open({ tenant_id: tenantId, reason: reason.trim(), minutes }), onSuccess: () => { setOpening(false); setReason(""); refresh(); } });
  const end = useMutation({ mutationFn: () => api.access.end(grant!.id), onSuccess: refresh });

  if (grant) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-card border border-[var(--pill-warning-ink)]/30 bg-[var(--pill-warning-bg)] px-4 py-2.5 text-body text-[var(--pill-warning-ink)]">
        <Eye className="size-4 flex-none" aria-hidden />
        <span className="min-w-0 flex-1">
          You&apos;re viewing {organization}&apos;s content until {until(grant.expires_at)}. <span className="opacity-80">Reason: {grant.reason}</span>
        </span>
        <Button size="sm" onClick={() => end.mutate()} disabled={end.isPending}>{end.isPending ? "Closing…" : "Close access"}</Button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 rounded-card bg-card px-4 py-2.5 [box-shadow:var(--shadow-card)]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-body text-muted">
        {me.can("support.access") ? <EyeOff className="size-4 flex-none" aria-hidden /> : <LockKeyhole className="size-4 flex-none" aria-hidden />}
        <span className="min-w-0 flex-1">
          Facts only: what ran, when, for how long and how it ended. {me.can("support.access")
            ? `Thoughts, records, messages and files stay closed until you open ${organization}'s content.`
            : "Your role can't open a customer's content."}
        </span>
        {me.can("support.access") && !opening && <Button size="sm" onClick={() => setOpening(true)}>Open content…</Button>}
      </div>
      {opening && (
        <form className="flex flex-col gap-2 @3xl:flex-row @3xl:items-end" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length >= 5) open.mutate(); }}>
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-meta text-muted">
            Why you need it (the organization sees this)
            <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={1000} required autoFocus className={field} placeholder="Ticket 1234: a record saved with the wrong total" />
          </label>
          <label className="flex flex-col gap-1 text-meta text-muted">
            For
            <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className={field}>
              {[15, 30, 60, 120, 240].map((m) => <option key={m} value={m}>{m < 60 ? `${m} minutes` : `${m / 60} hour${m === 60 ? "" : "s"}`}</option>)}
            </select>
          </label>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={reason.trim().length < 5 || open.isPending}>{open.isPending ? "Opening…" : "Open content"}</Button>
            <Button onClick={() => setOpening(false)}>Cancel</Button>
          </div>
        </form>
      )}
      {open.error && <p className="text-meta text-negative">{open.error.message}</p>}
    </div>
  );
}

/** A staff action that needs a reason: a button that opens a one-line form. */
export function ReasonAction({ label, busyLabel, confirmLabel, onRun, variant = "secondary", disabled }: {
  label: string; busyLabel: string; confirmLabel: string; onRun: (reason: string) => Promise<unknown>;
  variant?: "secondary" | "danger"; disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const run = useMutation({ mutationFn: () => onRun(reason.trim()), onSuccess: () => { setAsking(false); setReason(""); } });
  if (!asking) return <Button size="sm" variant={variant} disabled={disabled} onClick={() => setAsking(true)}>{label}</Button>;
  return (
    <form className="flex w-full flex-col gap-2 rounded-control bg-raised p-3 @2xl:flex-row @2xl:items-center" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length >= 5) run.mutate(); }}>
      <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={1000} required autoFocus aria-label="Why"
        placeholder="Why (kept in the audit log, and the organization sees it)" className={cn(field, "min-w-0 flex-1")} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant={variant} disabled={reason.trim().length < 5 || run.isPending}>{run.isPending ? busyLabel : confirmLabel}</Button>
        <Button size="sm" onClick={() => setAsking(false)}>Cancel</Button>
      </div>
      {run.error && <p className="text-meta text-negative @2xl:basis-full">{run.error.message}</p>}
    </form>
  );
}

/** A label and its value, for the facts at the top of a detail page. */
export function Fact({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", className)}>
      <dt className="text-meta text-muted">{label}</dt>
      <dd className="min-w-0 truncate text-body text-ink">{children}</dd>
    </div>
  );
}
