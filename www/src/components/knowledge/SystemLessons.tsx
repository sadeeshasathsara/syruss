"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import type { SystemLesson } from "@/lib/types";

/**
 * How Syruss works: lessons about the platform itself (its tools, refusals, when to commit and when
 * to ask), the same in every workspace. Syruss proposes them; an organization admin approves them.
 */
export function SystemLessons() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ["system-lessons"], queryFn: () => api.systemLessons.list() });
  const [draft, setDraft] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["system-lessons"] });
  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: SystemLesson["status"] }) => api.systemLessons.update(id, { status }),
    onSuccess: refresh,
  });
  const add = useMutation({ mutationFn: () => api.systemLessons.add(draft.trim()), onSuccess: () => { setDraft(""); refresh(); } });
  const items = list.data?.items ?? [];
  const proposed = items.filter((l) => l.status === "proposed");
  const active = items.filter((l) => l.status === "active");
  const manage = !!list.data?.can_manage;

  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="text-section font-semibold text-ink">How Syruss works</h2>
        <p className="mt-1 text-body text-muted">
          What Syruss has learned about working in this platform: its tools, refusals, when to save and when to ask. The same
          in every workspace, and never about your business.{manage ? " Syruss proposes new lessons; they apply once you approve them." : ""}
        </p>
      </div>
      {list.isLoading && <Bar className="h-24 w-full" />}
      {manage && proposed.length > 0 && (
        <ul className="flex flex-col rounded-card border border-control bg-card">
          {proposed.map((l) => (
            <li key={l.id} className="flex flex-col gap-2 border-b border-rule px-3 py-3 last:border-0">
              <div className="flex flex-wrap items-center gap-2">
                <Pill tone="info">Proposed by Syruss</Pill>
                {l.times_proposed > 1 && <span className="text-meta text-muted">{l.times_proposed} times</span>}
                <span className="text-meta text-muted">{ago(l.created_at)}</span>
              </div>
              <p className="text-body text-ink">{l.content}</p>
              {l.why && <p className="text-meta text-muted">Learned from: {l.why}</p>}
              <div className="flex gap-2">
                <Button size="sm" variant="primary" disabled={update.isPending} onClick={() => update.mutate({ id: l.id, status: "active" })}>Approve</Button>
                <Button size="sm" variant="ghost" disabled={update.isPending} onClick={() => update.mutate({ id: l.id, status: "retired" })}>Dismiss</Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {active.length > 0 && (
        <ul className="flex flex-col rounded-card border border-border bg-card">
          {active.map((l) => (
            <li key={l.id} className="flex items-start gap-3 border-b border-rule px-3 py-2.5 last:border-0">
              <p className="min-w-0 flex-1 text-body text-ink">{l.content}</p>
              {manage && (
                <Button size="sm" variant="ghost" disabled={update.isPending} onClick={() => update.mutate({ id: l.id, status: "retired" })}>Retire</Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {manage && (
        <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); if (draft.trim().length >= 5) add.mutate(); }}>
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={1000} className="min-h-16"
            aria-label="New lesson" placeholder="e.g. When a total on the page doesn't add up, send the document to review instead of correcting it." />
          {add.error && <p className="text-body text-negative">{add.error.message}</p>}
          <div><Button type="submit" size="sm" variant="secondary" disabled={draft.trim().length < 5 || add.isPending}>Add a lesson</Button></div>
        </form>
      )}
    </section>
  );
}
