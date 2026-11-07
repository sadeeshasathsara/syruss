"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { field } from "@/components/ops/Bits";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/format";

/**
 * A billing change staff make: its own fields, then why (kept in the platform audit log and shown in the organization's
 * activity). On success every billing view refreshes and the form closes.
 */
export function StaffForm({ title, submit, busy, onRun, onDone, children, ready = true, danger }: {
  title: string; submit: string; busy: string; onRun: (reason: string) => Promise<unknown>; onDone?: () => void;
  children?: React.ReactNode; ready?: boolean; danger?: boolean;
}) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const run = useMutation({
    mutationFn: () => onRun(reason.trim()),
    onSuccess: () => { setReason(""); queryClient.invalidateQueries({ queryKey: ["billing"] }); onDone?.(); },
  });
  return (
    <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (ready && reason.trim().length >= 5) run.mutate(); }}>
      <h3 className="text-body font-medium text-ink">{title}</h3>
      {children}
      <Label text="Why (the organization sees this)">
        <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={1000} required className={field}
          placeholder="Signed contract, ticket 1234…" />
      </Label>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm" variant={danger ? "danger" : "primary"} disabled={!ready || reason.trim().length < 5 || run.isPending}>
          {run.isPending ? busy : submit}
        </Button>
        {onDone && <Button size="sm" onClick={onDone}>Cancel</Button>}
        {run.error && <p className="text-meta text-negative">{run.error.message}</p>}
      </div>
    </form>
  );
}

export function Label({ text, children, className }: { text: string; children: React.ReactNode; className?: string }) {
  return <label className={cn("flex min-w-0 flex-col gap-1 text-meta text-muted", className)}>{text}{children}</label>;
}

/** Whole money in a form ("49.00"), kept as cents. */
export function MoneyInput({ cents, onChange }: { cents: number; onChange: (cents: number) => void }) {
  const [text, setText] = useState((cents / 100).toFixed(2));
  return (
    <input inputMode="decimal" value={text} className={field}
      onChange={(e) => { setText(e.target.value); const v = Number(e.target.value); if (Number.isFinite(v) && v >= 0) onChange(Math.round(v * 100)); }} />
  );
}
