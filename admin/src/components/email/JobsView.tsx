"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { field } from "@/components/ops/Bits";
import { Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { MailAccount, MailDirection, MailJobDef, MailJobSettings, MailOverview } from "@/lib/types";

const DIRECTION: Record<MailDirection, string> = { send: "Sends", read: "Reads", both: "Sends and reads" };
// The built-in jobs first, in this order; the ones added here after them
const ORDER = ["notifications", "support", "documents", "promotions", "accounts"];

function useRefresh() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["mail"] });
}

/** What a job needs, so a mailbox that can't do it isn't offered for it. */
function canServe(account: MailAccount, job: MailJobDef): boolean {
  return job.needs.every((n) => account.granted.includes(n));
}

/** The jobs mail does and the mailbox each uses; jobs of your own can be added. */
export function JobsView({ o }: { o: MailOverview }) {
  const keys = Object.keys(o.purposes).sort((a, b) => {
    const ia = ORDER.indexOf(a);
    const ib = ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return (
    <>
      <Panel title="Jobs">
        <ul className="flex flex-col divide-y divide-rule">
          {keys.map((key) => <Job key={`${key}:${JSON.stringify(o.purposes[key].settings)}`} id={key} job={o.purposes[key]} o={o} />)}
        </ul>
      </Panel>
      <NewJob taken={keys} />
    </>
  );
}

function Job({ id, job, o }: { id: string; job: MailJobDef; o: MailOverview }) {
  const refresh = useRefresh();
  const [confirming, setConfirming] = useState(false);
  const route = useMutation({ mutationFn: (account: string | null) => api.mail.route(id, account), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.mail.deleteJob(id), onSuccess: refresh });
  const serving = o.accounts.find((a) => a.id === job.account_id);
  return (
    <li className="flex flex-col gap-2 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 text-body font-medium text-ink">
            {job.label}
            <Pill tone="neutral">{DIRECTION[job.direction]}</Pill>
            {!job.system && <Pill tone="accent">Yours</Pill>}
          </p>
          {job.description && <p className="text-meta text-muted">{job.description}</p>}
        </div>
        <select aria-label={`Mailbox for ${job.label}`} className={cn(field, "w-72")} disabled={route.isPending}
          value={job.routed ? (job.account_id ?? "env") : "env"} onChange={(e) => route.mutate(e.target.value === "env" ? null : e.target.value)}>
          {o.accounts.map((a) => (
            <option key={a.id} value={a.from_environment ? "env" : a.id} disabled={!canServe(a, job)}>
              {a.address}{a.from_environment ? " (from the environment)" : ""}{canServe(a, job) ? "" : ` (can't ${job.needs.filter((n) => !a.granted.includes(n)).join(" or ")})`}
            </option>
          ))}
        </select>
        {!job.routed && <Pill tone="neutral">Fallback</Pill>}
        {job.reads && <Pill tone="positive">Reading</Pill>}
        {(job.direction !== "send") && !job.reads && <Pill tone="warning">Not reading</Pill>}
      </div>
      <p className="text-meta text-muted">
        {serving ? `Through ${serving.address}. ` : ""}
        {job.direction !== "read" && (job.last_sent_at ? `Last sent ${ago(job.last_sent_at).toLowerCase()}. ` : "Nothing sent yet. ")}
        {job.direction !== "send" && (job.last_read_at ? `Last read ${ago(job.last_read_at).toLowerCase()}.` : "Not read yet.")}
      </p>
      {job.last_error && <p className="text-meta text-negative">{job.last_error}</p>}
      <div className="flex flex-wrap items-start gap-3">
        <Settings id={id} job={job} />
        {!job.system && (confirming ? (
          <span className="flex items-center gap-2 text-meta text-muted">Remove this job?
            <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Yes, remove</Button>
            <Button size="sm" onClick={() => setConfirming(false)}>No</Button>
          </span>
        ) : <Button size="sm" onClick={() => setConfirming(true)}>Remove</Button>)}
      </div>
      <Problem error={route.error ?? remove.error} />
    </li>
  );
}

function Settings({ id, job }: { id: string; job: MailJobDef }) {
  const refresh = useRefresh();
  const [v, setV] = useState({ sender_name: job.settings.sender_name ?? "", reply_to: job.settings.reply_to ?? "", poll_seconds: String(job.settings.poll_seconds ?? "") });
  const save = useMutation({
    mutationFn: () => {
      const settings: MailJobSettings = { sender_name: v.sender_name.trim(), reply_to: v.reply_to.trim() };
      if (job.reads) settings.poll_seconds = v.poll_seconds ? Number(v.poll_seconds) : undefined;
      return api.mail.updateJob(id, { settings });
    },
    onSuccess: refresh,
  });
  return (
    <details className="min-w-0 flex-1 text-body">
      <summary className="cursor-pointer text-meta text-muted hover:text-ink">Settings of this job</summary>
      <form className="mt-2 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <label className="flex flex-col gap-1 text-meta text-muted">Sender name
          <input value={v.sender_name} className={cn(field, "w-48")} placeholder="Syruss" onChange={(e) => setV({ ...v, sender_name: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-meta text-muted">Replies go to
          <input value={v.reply_to} className={cn(field, "w-56")} placeholder="the sender's address" onChange={(e) => setV({ ...v, reply_to: e.target.value })} />
        </label>
        {job.reads && (
          <label className="flex flex-col gap-1 text-meta text-muted">Read every (seconds)
            <input type="number" min={15} value={v.poll_seconds} className={cn(field, "w-28")} placeholder="60" onChange={(e) => setV({ ...v, poll_seconds: e.target.value })} />
          </label>
        )}
        <Button type="submit" size="sm" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
      </form>
      <Problem error={save.error} />
    </details>
  );
}

const slug = (text: string) => text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 39);

function NewJob({ taken }: { taken: string[] }) {
  const refresh = useRefresh();
  const [label, setLabel] = useState("");
  const [direction, setDirection] = useState<MailDirection>("send");
  const [description, setDescription] = useState("");
  const key = slug(label);
  const create = useMutation({
    mutationFn: () => api.mail.createJob({ key, label: label.trim(), direction, description: description.trim() || undefined }),
    onSuccess: () => { refresh(); setLabel(""); setDescription(""); },
  });
  const problem = key.length < 2 ? "Give it a name." : /^[a-z]/.test(key) ? taken.includes(key) ? `There is a job called “${key}” already.` : "" : "A name starts with a letter.";
  return (
    <Panel title="Add a job">
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <p className="text-meta text-muted">A job of your own gets a mailbox and settings like the built-in ones. Syruss acts only on the jobs it knows; yours sends and reads for whatever you connect to it.</p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-meta text-muted">Name
            <input value={label} className={cn(field, "w-56")} placeholder="Partner updates" onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-meta text-muted">It
            <select value={direction} className={field} onChange={(e) => setDirection(e.target.value as MailDirection)}>
              <option value="send">Sends</option><option value="read">Reads</option><option value="both">Sends and reads</option>
            </select>
          </label>
          <label className="flex min-w-48 flex-1 flex-col gap-1 text-meta text-muted">What it is for (optional)
            <input value={description} className={field} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <Button type="submit" variant="primary" disabled={!!problem || create.isPending}>{create.isPending ? "Adding…" : "Add job"}</Button>
        </div>
        {label && <p className="text-meta text-muted">{problem || `Its key will be “${key}”.`}</p>}
        <Problem error={create.error} />
      </form>
    </Panel>
  );
}
