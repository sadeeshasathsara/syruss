"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";

/**
 * The second owner's approval: whether risky actions wait for another owner, and which. Owners set it; while it is on,
 * changing or turning it off waits for a second owner as well.
 */
export function ApprovalRulePanel() {
  const me = useStaff();
  const manage = me.can("staff.manage");
  const queryClient = useQueryClient();
  const rule = useQuery({ queryKey: ["approvals", "rule"], queryFn: api.approvals.rule });
  const [editing, setEditing] = useState(false);
  const [on, setOn] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const save = useMutation({
    mutationFn: () => api.approvals.setRule({ on, categories: chosen, reason: reason.trim() }),
    onSuccess: () => { setEditing(false); setReason(""); queryClient.invalidateQueries({ queryKey: ["approvals"] }); queryClient.invalidateQueries({ queryKey: ["audit"] }); },
  });
  const r = rule.data;

  return (
    <div id="approvals" className="scroll-mt-4">
      <Panel title="Second owner's approval" aside={r ? <Pill tone={r.on ? "positive" : "neutral"}>{r.on ? "On" : "Off"}</Pill> : undefined}>
        {!r ? <Bar className="h-16 w-full" /> : (
          <div className="flex flex-col gap-3">
            <p className="text-body text-muted">
              {r.on
                ? `Risky actions wait for an owner other than the person who asked, for up to ${r.hours} hours, and are carried out once approved. Changing or turning off this rule waits too.`
                : "When on, the risky actions you choose wait for an owner other than the person who asked. It needs at least two owners."}
            </p>
            {!editing ? (
              <>
                {r.on && (
                  <ul className="flex flex-col gap-1">
                    {r.categories.map((c) => <li key={c} className="text-body text-ink">· {r.all_categories[c]}</li>)}
                  </ul>
                )}
                <p className="text-meta text-muted">{r.approvers} owner{r.approvers === 1 ? "" : "s"} can approve.</p>
                {manage && (
                  <div>
                    <Button size="sm" onClick={() => {
                      setOn(true);  // opening the form is for turning it on, or changing it while on
                      setChosen(r.on ? r.categories : Object.keys(r.all_categories));
                      setEditing(true);
                    }}>{r.on ? "Change…" : "Turn on…"}</Button>
                  </div>
                )}
              </>
            ) : (
              <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length >= 5) save.mutate(); }}>
                <label className="flex items-center gap-2 text-body text-ink">
                  <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} className="size-4 accent-[var(--accent)]" />
                  Ask a second owner before risky actions
                </label>
                <fieldset disabled={!on} className={cn("flex flex-col gap-1.5", !on && "opacity-60")}>
                  <legend className="mb-1 text-meta text-muted">What waits for approval</legend>
                  {Object.entries(r.all_categories).map(([key, label]) => (
                    <label key={key} className="flex items-center gap-2 text-body text-ink">
                      <input type="checkbox" checked={chosen.includes(key)} className="size-4 accent-[var(--accent)]"
                             onChange={(e) => setChosen((now) => e.target.checked ? [...now, key] : now.filter((k) => k !== key))} />
                      {label}
                    </label>
                  ))}
                </fieldset>
                <label className="flex flex-col gap-1 text-meta text-muted">
                  Why (kept in the audit log)
                  <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={1000} required className={field} />
                </label>
                <div className="flex gap-2">
                  <Button type="submit" size="sm" variant="primary" disabled={reason.trim().length < 5 || (on && !chosen.length) || save.isPending}>
                    {save.isPending ? "Saving…" : "Save"}
                  </Button>
                  <Button size="sm" onClick={() => { setEditing(false); save.reset(); }}>Cancel</Button>
                </div>
                {on && r.approvers < 2 && <p className="text-meta text-[var(--pill-warning-ink)]">Invite a second owner first: someone has to be able to approve.</p>}
              </form>
            )}
            <Problem error={save.error} />
          </div>
        )}
      </Panel>
    </div>
  );
}
