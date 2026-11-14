"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { Fact, ReasonAction } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { describeAction } from "@/lib/audit";
import { ago, dateTime } from "@/lib/format";

const EVENT_TONE: Record<string, "negative" | "warning" | "neutral" | "positive"> = {
  code_failed: "negative", devices_removed: "warning", device_removed: "warning", device_trusted: "positive", first_device: "positive",
};

/** One account: where they belong, the browsers they confirmed, their security events, and what staff did. */
export default function PersonPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useStaff();
  const queryClient = useQueryClient();
  const page = useQuery({ queryKey: ["console", "person", id], queryFn: () => api.console.person(id) });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["console", "person", id] });
    queryClient.invalidateQueries({ queryKey: ["accounts"] });
  };
  if (page.isError) return <ErrorNote message={page.error.message} onRetry={() => page.refetch()} />;
  const p = page.data;
  if (!p) return <div className="flex flex-col gap-4"><Bar className="h-10 w-72" /><Bar className="h-48 w-full" /></div>;
  const who = p.person;
  const manage = me.can("users.manage") && who.id !== me.user_id && who.status !== "deleted";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        back={{ href: "/accounts", label: "Accounts" }}
        title={who.name}
        subtitle={<span className="flex flex-wrap items-center gap-2">
          <Pill tone={who.status === "active" ? "positive" : "negative"}>{who.status === "active" ? "Active" : who.status === "disabled" ? "Disabled" : "Deleted"}</Pill>
          <span>{who.email}{who.email_verified ? "" : " (not confirmed)"}</span>
          {who.staff_role && <Pill tone="accent">Platform staff: {who.staff_role}</Pill>}
        </span>}
        actions={manage ? (
          <div className="flex flex-wrap gap-2">
            {p.browsers.length > 0 && (
              <ReasonAction label="Forget their browsers" busyLabel="Forgetting…" confirmLabel="Forget them"
                onRun={(r) => api.console.forgetBrowsers(who.id, r).then(refresh)} />
            )}
            {who.status === "active" ? (
              <ReasonAction label="Disable" busyLabel="Disabling…" confirmLabel="Disable the account" variant="danger" onRun={(r) => api.accounts.disable(who.id, r).then(refresh)} />
            ) : (
              <ReasonAction label="Enable" busyLabel="Enabling…" confirmLabel="Enable the account" onRun={(r) => api.accounts.enable(who.id, r).then(refresh)} />
            )}
          </div>
        ) : undefined}
      />

      <Panel title="Account">
        <dl className="grid grid-cols-2 gap-3 @3xl:grid-cols-4">
          <Fact label="Joined">{dateTime(who.created_at)}</Fact>
          <Fact label="Last seen">{who.last_seen_at ? ago(who.last_seen_at) : "Never"}</Fact>
          <Fact label="Time zone">{who.timezone}</Fact>
          <Fact label="Account id"><span className="font-mono text-meta">{who.id}</span></Fact>
        </dl>
      </Panel>

      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-2">
        <Panel title="Organizations and workspaces">
          {!p.organizations.length ? <p className="text-body text-muted">In no organization.</p> : (
            <ul className="flex flex-col divide-y divide-rule">
              {p.organizations.map((o) => (
                <li key={o.id} className="flex flex-col gap-1 py-2">
                  <span className="flex items-center gap-2">
                    <Link href={`/accounts/organizations/${o.id}`} className="min-w-0 flex-1 truncate text-body font-medium text-ink hover:underline">{o.name}</Link>
                    <Pill tone={o.role === "owner" ? "accent" : "neutral"}>{o.role}</Pill>
                    {o.status !== "active" && <Pill tone="negative">{o.status}</Pill>}
                  </span>
                  {p.workspaces.filter((w) => w.tenant_id === o.id).map((w) => (
                    <span key={w.id} className="pl-3 text-meta text-muted">{w.name} · {w.role}</span>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Confirmed browsers" aside={<span className="num">{p.browsers.length}</span>}>
          {!p.browsers.length ? <p className="text-body text-muted">None confirmed now: the next sign-in asks for an emailed code.</p> : (
            <ul className="flex flex-col divide-y divide-rule">
              {p.browsers.map((b) => (
                <li key={b.id} className="flex flex-wrap items-baseline gap-x-3 py-2">
                  <span className="min-w-0 flex-1 truncate text-body text-ink">{b.label}</span>
                  <span className="num text-meta text-muted">{b.ip || "no address"} · seen {ago(b.last_seen_at).toLowerCase()}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-2">
        <Panel title="Security events">
          {!p.security_events.length ? <p className="text-body text-muted">Nothing recorded.</p> : (
            <ul className="flex flex-col divide-y divide-rule">
              {p.security_events.map((e, i) => (
                <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                  <Pill tone={EVENT_TONE[e.kind] ?? "neutral"}>{e.kind.replace(/_/g, " ")}</Pill>
                  <span className="min-w-0 flex-1 text-body text-ink">{e.summary}</span>
                  <span className="num text-meta text-muted">{e.ip ? `${e.ip} · ` : ""}{ago(e.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="What staff did">
          {!p.staff_actions.length ? <p className="text-body text-muted">Nothing yet.</p> : (
            <ul className="flex flex-col divide-y divide-rule">
              {p.staff_actions.map((a) => (
                <li key={a.id} className="py-2 text-body">
                  <span className="font-medium text-ink">{a.actor_name}</span> <span className="text-ink">{describeAction(a)}</span>
                  <span className="text-muted">{a.reason ? ` · ${a.reason}` : ""} · {ago(a.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
