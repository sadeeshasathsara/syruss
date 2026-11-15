"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { MailJob } from "@/lib/types";
import { AccountsView } from "./AccountsView";
import { AppsView } from "./AppsView";
import { JobsView } from "./JobsView";
import { Select } from "@/components/ui/Select";

const STATUS: Record<MailJob["status"], { label: string; tone: "positive" | "warning" | "negative" | "neutral" | "info" }> = {
  queued: { label: "Queued", tone: "info" }, sending: { label: "Sending", tone: "info" }, retrying: { label: "Trying again", tone: "warning" },
  sent: { label: "Sent", tone: "positive" }, dead: { label: "Not sent", tone: "negative" },
};

export type EmailView = "jobs" | "accounts" | "apps" | "log";

const SUBTITLE: Record<EmailView, string> = {
  jobs: "What Syruss uses mail for. Each job has a mailbox of its own: choose which one does what, and add jobs of your own.",
  accounts: "The mailboxes Syruss sends and reads mail through. Connect Google or Microsoft, or any mail server. Passwords are sealed in this browser.",
  apps: "The Google and Microsoft apps Syruss signs people in through, so connecting a mailbox is a button, not an app password. Secrets are sealed in this browser.",
  log: "What the mail service sent, to whom and how it went. The text itself isn't kept.",
};

/**
 * The Email section (owners only): the jobs mail does and the mailbox each uses, the connected mailboxes, and how recent mail went
 * (the sidebar's sub-tabs). Everything on it is what the mail service says about itself; a change is carried out there, and the page
 * is told live when something changed, so it is always right.
 */
export function EmailPage({ view }: { view: EmailView }) {
  const queryClient = useQueryClient();
  const live = useLive({ scope: "mail" }, (e) => {
    if (e.kind === "mail.changed") queryClient.invalidateQueries({ queryKey: ["mail"] });
  });
  const overview = useQuery({ queryKey: ["mail", "overview"], queryFn: api.mail.overview, enabled: view !== "log" });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Email" subtitle={SUBTITLE[view]}
        actions={<Pill tone={live.state === "live" ? "positive" : "neutral"}>{live.state === "live" ? "Live" : live.state === "closed" ? "Not live" : "Connecting…"}</Pill>} />
      {view === "log" ? <Deliveries /> : overview.isError ? <ErrorNote message={overview.error.message} onRetry={() => overview.refetch()} />
        : !overview.data ? <Bar className="h-64 w-full" /> : view === "jobs" ? <JobsView o={overview.data} />
          : view === "apps" ? <AppsView o={overview.data} /> : <AccountsView o={overview.data} />}
    </div>
  );
}

function Deliveries() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const jobs = useQuery({ queryKey: ["mail", "jobs", status], queryFn: () => api.mail.jobs(status || undefined), refetchInterval: 30_000 });
  const retry = useMutation({ mutationFn: (id: string) => api.mail.retry(id), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["mail"] }) });
  return (
    <Panel title="Delivery log" aside={
      <Select aria-label="Show" value={status} onValueChange={(v) => setStatus(v)}>
        <option value="">Everything</option><option value="dead">Not sent</option><option value="retrying">Trying again</option><option value="sent">Sent</option>
      </Select>}>
      {jobs.isError ? <ErrorNote message={jobs.error.message} onRetry={() => jobs.refetch()} /> : !jobs.data ? <Bar className="h-32 w-full" /> : !jobs.data.length ? (
        <Empty title="Nothing yet">Mail the service sends shows here: who it went to, what about, and how it went. The text itself isn&apos;t kept.</Empty>
      ) : (
        <ul className="flex flex-col divide-y divide-rule">
          {jobs.data.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              <Pill tone={STATUS[j.status].tone}>{STATUS[j.status].label}</Pill>
              <span className="min-w-0 flex-1 truncate text-body text-ink">{j.subject}</span>
              <span className="text-meta text-muted">{j.to} · {j.purpose}</span>
              <span className="text-meta text-muted">{dateTime(j.created_at)}</span>
              {j.retryable && <Button size="sm" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>Send again</Button>}
              {j.last_error && j.status !== "sent" && <p className="w-full text-meta text-negative">{j.last_error}{j.attempts > 1 ? ` (${j.attempts} tries)` : ""}</p>}
            </li>
          ))}
        </ul>
      )}
      <Problem error={retry.error} />
    </Panel>
  );
}
