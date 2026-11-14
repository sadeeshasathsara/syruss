"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ApprovalRulePanel } from "@/components/ops/ApprovalRule";
import { ReasonAction, field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn, dateTime } from "@/lib/format";
import type { Announcement, PlatformControls, Severity, SwitchName } from "@/lib/types";
import { Problem } from "@/components/ui/Problem";

const SWITCHES: { key: SwitchName; label: string; on: string; off: string }[] = [
  { key: "signups", label: "New sign-ups", on: "People can create accounts and organizations.", off: "Paused: new accounts and organizations are refused. Invitations still work." },
  { key: "agent", label: "The agent", on: "Documents are read as they arrive.", off: "Paused for everyone: documents wait in order and are read when it is switched back on." },
  { key: "intake", label: "Documents from other systems", on: "API keys, e-mail and SFTP channels take documents in.", off: "Paused: senders are told to try again in a few minutes." },
];
const SEVERITY: Record<Severity, "info" | "warning" | "negative"> = { info: "info", warning: "warning", critical: "negative" };

/** The platform's own switches, and what it tells everyone using it. Every change asks for a reason and a recent sign-in. */
export default function PlatformPage() {
  const me = useStaff();
  const manage = me.can("system.manage");
  const queryClient = useQueryClient();
  const controls = useQuery({ queryKey: ["console", "platform"], queryFn: api.console.platform });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["console", "platform"] });
    queryClient.invalidateQueries({ queryKey: ["audit"] });
  };
  if (controls.isError) return <ErrorNote message={controls.error.message} onRetry={() => controls.refetch()} />;
  const c = controls.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Platform controls"
        subtitle={manage ? "Maintenance, the switches every customer feels, and announcements. Changes reach every server within seconds." : "What is switched on. Only staff with the System role change these."} />
      {!c ? <Bar className="h-64 w-full" /> : (
        <>
          <Maintenance c={c} manage={manage} onDone={refresh} />
          <ApprovalRulePanel />
          <Panel title="Switches" bodyClassName="p-0">
            <ul className="flex flex-col divide-y divide-rule">
              {SWITCHES.map((s) => {
                const on = c.switches[s.key];
                return (
                  <li key={s.key} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
                    <div className="flex min-w-0 flex-1 flex-col">
                      <span className="text-body font-medium text-ink">{s.label}</span>
                      <span className="text-meta text-muted">{on ? s.on : s.off}</span>
                    </div>
                    <Pill tone={on ? "positive" : "warning"}>{on ? "On" : "Paused"}</Pill>
                    {manage && (
                      <ReasonAction label={on ? "Pause" : "Switch on"} busyLabel="Saving…" confirmLabel={on ? "Pause it" : "Switch it on"}
                        variant={on ? "danger" : "secondary"} onRun={(r) => api.console.setSwitch(s.key, !on, r).then(refresh)} />
                    )}
                  </li>
                );
              })}
            </ul>
            {c.agent_paused_orgs.length > 0 && (
              <p className="border-t border-rule px-4 py-3 text-body text-muted">
                The agent is also paused for{" "}
                {c.agent_paused_orgs.map((o, i) => <span key={o.id}>{i ? ", " : ""}<Link href={`/accounts/organizations/${o.id}`} className="text-ink underline-offset-2 hover:underline">{o.name}</Link></span>)}.
              </p>
            )}
          </Panel>
          <Announcements list={c.announcements} manage={manage} onDone={refresh} />
          <Lessons manage={manage} />
        </>
      )}
    </div>
  );
}

/** How Syruss works: the lessons every workspace's agent is briefed with. The agent proposes; staff approve. */
function Lessons({ manage }: { manage: boolean }) {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ["lessons"], queryFn: api.lessons.list });
  const [content, setContent] = useState("");
  const done = () => queryClient.invalidateQueries({ queryKey: ["lessons"] });
  const set = useMutation({ mutationFn: ({ id, status }: { id: string; status: "active" | "retired" }) => api.lessons.set(id, status), onSuccess: done });
  const add = useMutation({ mutationFn: () => api.lessons.add(content.trim()), onSuccess: () => { setContent(""); done(); } });
  const items = list.data?.items ?? [];
  const proposed = items.filter((l) => l.status === "proposed");
  const active = items.filter((l) => l.status === "active");
  return (
    <Panel title="How Syruss works" aside={<span className="text-meta text-muted">Lessons every workspace&apos;s agent is briefed with</span>}>
      {proposed.length > 0 && (
        <div className="mb-4">
          <p className="mb-2 text-body font-medium text-ink">Proposed by the agent <span className="num text-muted">{proposed.length}</span></p>
          <ul className="flex flex-col divide-y divide-rule">
            {proposed.map((l) => (
              <li key={l.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-body text-ink">{l.content}</p>
                  {(l.why || l.times_proposed) && <p className="text-meta text-muted">{l.why}{l.times_proposed && l.times_proposed > 1 ? ` · proposed ${l.times_proposed} times` : ""}</p>}
                </div>
                {manage && <Button size="sm" variant="primary" onClick={() => set.mutate({ id: l.id, status: "active" })}>Approve</Button>}
                {manage && <Button size="sm" onClick={() => set.mutate({ id: l.id, status: "retired" })}>Turn down</Button>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <ul className="flex flex-col divide-y divide-rule">
        {active.map((l) => (
          <li key={l.id} className="flex items-start gap-3 py-2">
            <p className="min-w-0 flex-1 text-body text-ink">{l.content}</p>
            {manage && <Button size="sm" onClick={() => set.mutate({ id: l.id, status: "retired" })}>Retire</Button>}
          </li>
        ))}
      </ul>
      {manage && (
        <form className="mt-3 flex flex-col gap-2 @3xl:flex-row" onSubmit={(e) => { e.preventDefault(); if (content.trim().length >= 5) add.mutate(); }}>
          <input value={content} onChange={(e) => setContent(e.target.value)} maxLength={1000} placeholder="A lesson for every workspace's agent" aria-label="New lesson" className={cn(field, "min-w-0 flex-1")} />
          <Button type="submit" size="sm" disabled={content.trim().length < 5 || add.isPending}>Add it</Button>
        </form>
      )}
      {(set.error ?? add.error) && <Problem error={(set.error ?? add.error)!} className="mt-2" />}
    </Panel>
  );
}

function Maintenance({ c, manage, onDone }: { c: PlatformControls; manage: boolean; onDone: () => void }) {
  const [message, setMessage] = useState(c.maintenance.message || "Syruss is being upgraded. You can look around; changes are paused for a few minutes.");
  return (
    <Panel title="Maintenance" aside={<Pill tone={c.maintenance.on ? "warning" : "positive"}>{c.maintenance.on ? "On: read-only" : "Off"}</Pill>}>
      <p className="text-body text-muted">
        {c.maintenance.on
          ? "Everyone can sign in and look; anything that would change something is refused with your message. The agent and documents from other systems wait. Staff tools keep working."
          : "Turn it on before work that needs nothing to change (a database upgrade, a restore). People see your message at the top of the app."}
      </p>
      {manage && (
        <div className="mt-3 flex flex-col gap-2">
          {!c.maintenance.on && (
            <input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={500} aria-label="The message people see" className={cn(field, "w-full")} />
          )}
          <ReasonAction label={c.maintenance.on ? "End maintenance" : "Start maintenance"} busyLabel="Saving…"
            confirmLabel={c.maintenance.on ? "End it" : "Start it"} variant={c.maintenance.on ? "secondary" : "danger"}
            onRun={(r) => api.console.setMaintenance(!c.maintenance.on, message, r).then(onDone)} />
        </div>
      )}
    </Panel>
  );
}

function Announcements({ list, manage, onDone }: { list: Announcement[]; manage: boolean; onDone: () => void }) {
  const end = useMutation({ mutationFn: (id: string) => api.console.endAnnouncement(id), onSuccess: onDone });
  return (
    <Panel title="Announcements" aside={<span className="text-meta text-muted">Shown at the top of the app</span>}>
      {manage && <NewAnnouncement onDone={onDone} />}
      {!list.length ? <p className="mt-3 text-body text-muted">None yet.</p> : (
        <ul className="mt-3 flex flex-col divide-y divide-rule">
          {list.map((a) => (
            <li key={a.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2.5">
              <Pill tone={SEVERITY[a.severity]}>{a.severity}</Pill>
              <div className="min-w-0 flex-1">
                <p className="text-body font-medium text-ink">{a.title}</p>
                {a.body && <p className="text-body text-muted">{a.body}</p>}
                <p className="text-meta text-muted">
                  To {a.organization ?? "everyone"} · {a.state === "scheduled" ? `from ${dateTime(a.starts_at)}` : `since ${dateTime(a.starts_at)}`}
                  {a.ends_at ? ` until ${dateTime(a.ends_at)}` : ""} · by {a.created_by_name}
                </p>
              </div>
              <Pill tone={a.state === "live" ? "positive" : a.state === "scheduled" ? "info" : "neutral"}>{a.state}</Pill>
              {manage && a.state !== "ended" && <Button size="sm" onClick={() => end.mutate(a.id)} disabled={end.isPending}>{a.state === "live" ? "End it" : "Call it off"}</Button>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function NewAnnouncement({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [severity, setSeverity] = useState<Severity>("info");
  const [org, setOrg] = useState("");
  const [starts, setStarts] = useState("");
  const [ends, setEnds] = useState("");
  const [reason, setReason] = useState("");
  const orgs = useQuery({ queryKey: ["accounts", "organizations", ""], queryFn: () => api.accounts.organizations() });
  const create = useMutation({
    mutationFn: () => api.console.announce({
      title: title.trim(), body: body.trim(), severity, tenant_id: org || null, reason: reason.trim(),
      starts_at: starts ? new Date(starts).toISOString() : null, ends_at: ends ? new Date(ends).toISOString() : null,
    }),
    onSuccess: () => { setTitle(""); setBody(""); setReason(""); setStarts(""); setEnds(""); onDone(); },
  });
  const ready = title.trim().length >= 3 && reason.trim().length >= 5;
  return (
    <form className="grid grid-cols-1 gap-2 rounded-control bg-raised p-3 @3xl:grid-cols-2" onSubmit={(e) => { e.preventDefault(); if (ready) create.mutate(); }}>
      <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} placeholder="Title" aria-label="Title" className={cn(field, "@3xl:col-span-2")} />
      <textarea value={body} onChange={(e) => setBody(e.target.value)} maxLength={2000} rows={2} placeholder="More detail (optional)" aria-label="Detail"
        className="rounded-control border border-control bg-sheet px-2.5 py-2 text-body text-ink outline-none focus-visible:border-accent @3xl:col-span-2" />
      <label className="flex flex-col gap-1 text-meta text-muted">How serious
        <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)} className={field}>
          <option value="info">Information</option><option value="warning">Warning</option><option value="critical">Critical</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-meta text-muted">To
        <select value={org} onChange={(e) => setOrg(e.target.value)} className={field}>
          <option value="">Everyone</option>
          {(orgs.data ?? []).filter((o) => o.status === "active").map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-meta text-muted">From (empty: now)
        <input type="datetime-local" value={starts} onChange={(e) => setStarts(e.target.value)} className={field} />
      </label>
      <label className="flex flex-col gap-1 text-meta text-muted">Until (empty: until ended)
        <input type="datetime-local" value={ends} onChange={(e) => setEnds(e.target.value)} className={field} />
      </label>
      <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} placeholder="Why (kept in the audit log)" aria-label="Why" className={cn(field, "@3xl:col-span-2")} />
      {create.error && <Problem error={create.error} className="@3xl:col-span-2" />}
      <div className="@3xl:col-span-2"><Button type="submit" size="sm" variant="primary" disabled={!ready || create.isPending}>Announce it</Button></div>
    </form>
  );
}

