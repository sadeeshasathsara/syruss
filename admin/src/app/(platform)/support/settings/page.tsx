"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { SupportTabs } from "@/components/support/Bits";
import { Bar, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { SupportSettings, TicketPriority } from "@/lib/types";

const PRIORITIES: TicketPriority[] = ["urgent", "high", "normal", "low"];

/** Service targets (hours to the first answer and to a solution, by priority), when waiting tickets close, and the mailbox. */
export default function SupportSettingsPage() {
  const me = useStaff();
  const settings = useQuery({ queryKey: ["support", "settings"], queryFn: api.support.settings });
  const s = settings.data;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Support" subtitle="Service targets and the support mailbox. Owners change them." />
      <SupportTabs />
      {!s ? <Bar className="h-64 w-full" /> : (
        <>
          <Panel title="Support mailbox" aside={<Pill tone={s.mailbox.reading ? "positive" : "warning"}>{s.mailbox.reading ? "Reading" : "Not read"}</Pill>}>
            <p className="text-body text-muted">
              Ticket email is sent from <span className="font-medium text-ink">{s.mailbox.address}</span>.{" "}
              {s.mailbox.reading ? "Replies sent to it are read every minute and added to their tickets."
                : "Replies to it aren't read yet: set SUPPORT_IMAP_* in the server's environment (see server/.env.example)."}
            </p>
          </Panel>
          <Targets s={s} canChange={me.can("staff.manage")} />
        </>
      )}
    </div>
  );
}

function Targets({ s, canChange }: { s: SupportSettings; canChange: boolean }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState({ first_response_hours: s.first_response_hours, resolve_hours: s.resolve_hours,
    auto_solve_days: s.auto_solve_days, auto_close_days: s.auto_close_days });
  const [reason, setReason] = useState("");
  const save = useMutation({ mutationFn: () => api.support.setSettings({ ...draft, reason: reason.trim() }),
    onSuccess: () => { setReason(""); queryClient.invalidateQueries({ queryKey: ["support", "settings"] }); } });
  const hours = (key: "first_response_hours" | "resolve_hours", p: TicketPriority) => (
    <input type="number" min={1} max={720} value={draft[key][p]} disabled={!canChange} aria-label={`${key} ${p}`}
      onChange={(e) => setDraft({ ...draft, [key]: { ...draft[key], [p]: Number(e.target.value) } })} className={cn(field, "w-20")} />
  );
  return (
    <Panel title="Service targets">
      <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length >= 5) save.mutate(); }}>
        <table className="w-full max-w-xl text-body">
          <thead><tr className="text-left text-meta text-muted"><th className="py-1 font-normal">Priority</th><th className="font-normal">First answer within (hours)</th><th className="font-normal">Solved within (hours)</th></tr></thead>
          <tbody>
            {PRIORITIES.map((p) => (
              <tr key={p}><td className="py-1 capitalize text-ink">{p}</td><td>{hours("first_response_hours", p)}</td><td>{hours("resolve_hours", p)}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="flex flex-wrap gap-4">
          <label className="flex flex-col gap-1 text-meta text-muted">Mark solved after waiting for the customer (days)
            <input type="number" min={1} max={60} value={draft.auto_solve_days} disabled={!canChange} onChange={(e) => setDraft({ ...draft, auto_solve_days: Number(e.target.value) })} className={cn(field, "w-24")} />
          </label>
          <label className="flex flex-col gap-1 text-meta text-muted">Close solved tickets after (days)
            <input type="number" min={1} max={180} value={draft.auto_close_days} disabled={!canChange} onChange={(e) => setDraft({ ...draft, auto_close_days: Number(e.target.value) })} className={cn(field, "w-24")} />
          </label>
        </div>
        {canChange && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-meta text-muted">Why (kept in the audit log)
              <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={500} required className={field} />
            </label>
            <Button type="submit" variant="primary" size="sm" disabled={reason.trim().length < 5 || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
          </div>
        )}
        <Problem error={save.error} />
      </form>
    </Panel>
  );
}
