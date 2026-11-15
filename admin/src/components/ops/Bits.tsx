"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/format";
import type { LiveState } from "@/lib/live";
import { Problem } from "@/components/ui/Problem";

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
      {run.error && <Problem error={run.error} className="@2xl:basis-full" />}
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
