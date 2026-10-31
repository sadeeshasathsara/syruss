"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowRight, Check } from "lucide-react";
import Link from "next/link";
import { NoAccess, SettingsHeader } from "@/components/settings/kit";
import { Bar, ErrorNote, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { ConnectionsOverview } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

export function useOverview(enabled = true) {
  const { workspace } = useWorkspace();
  return useQuery({ queryKey: ["connections", workspace.id, "overview"], queryFn: () => api.connections.overview(workspace.id), enabled, refetchInterval: 30_000 });
}

const STATE: Record<ConnectionsOverview["state"], { tone: "positive" | "warning" | "negative"; label: string; text: string }> = {
  ok: { tone: "positive", label: "All good", text: "Documents are coming in and going out without trouble." },
  attention: { tone: "warning", label: "Needs a look", text: "Something was refused or paused. Nothing is lost." },
  problem: { tone: "negative", label: "Needs attention", text: "Some records could not be sent, or a destination is stopped." },
};

function Stage({ title, href, figure, caption, lines, tone }: { title: string; href: string; figure: number | string; caption: string; lines: [string, number | string, boolean?][]; tone?: "problem" }) {
  return (
    <Link href={href} className={cn("group flex min-w-0 flex-1 flex-col gap-3 rounded-card border border-border bg-card [box-shadow:var(--shadow-card)] p-4 transition-colors hover:border-control", tone === "problem" ? "border-[var(--negative)]" : "border-border")}>
      <span className="flex items-center justify-between text-meta font-medium uppercase tracking-wide text-muted">{title}<ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" /></span>
      <span>
        <span className="num block text-figure font-semibold text-ink">{figure}</span>
        <span className="block text-body text-muted">{caption}</span>
      </span>
      <dl className="mt-auto flex flex-col gap-1 border-t border-rule pt-3 text-body">
        {lines.map(([label, value, bad]) => (
          <div key={label} className="flex justify-between gap-3"><dt className="text-muted">{label}</dt><dd className={cn("num font-medium", bad ? "text-negative" : "text-ink")}>{value}</dd></div>
        ))}
      </dl>
    </Link>
  );
}

function Connector() {
  return (
    <span aria-hidden className="flex flex-none items-center justify-center text-faint @3xl:w-8">
      <ArrowDown className="size-5 @3xl:hidden" />
      <ArrowRight className="hidden size-5 @3xl:block" />
    </span>
  );
}

export function OverviewPage() {
  const { workspace, href } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const allowed = can("integrations.manage");
  const overview = useOverview(allowed);
  const title = "Overview";
  const description = "Documents coming in, records going out, and anything held back along the way.";
  if (!allowed) return <div className="flex flex-col"><SettingsHeader title={title} description={description} /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;
  const o = overview.data;
  const settling = workspace.needs_review + workspace.open_questions + (workspace.open_conversations ?? 0);

  if (overview.isLoading) return <div className="flex flex-col gap-3"><Bar className="h-10 w-64" /><Bar className="h-44 w-full" /></div>;
  if (overview.isError || !o) return <ErrorNote message="Couldn’t load the overview." onRetry={() => overview.refetch()} />;
  const fresh = o.receive.channels === 0 && o.publish.destinations === 0;
  const state = STATE[o.state];

  return (
    <div className="flex flex-col gap-6">
      <SettingsHeader title={title} description={description} actions={!fresh && <Pill tone={state.tone}>{state.label}</Pill>} />

      {fresh ? (
        <Panel title="Get connected">
          <ol className="flex flex-col gap-4">
            {[
              ["Open a way in", "Create a channel and give the system that sends documents a key.", "connections/receive", "Create a channel"],
              ["Choose where approved data goes", "Connect a webhook, an API or your accounting system. Nothing is sent until a document is approved.", "connections/publish/new", "Connect a system"],
              ["Try it with a test key", "A test key is read like a real one but never published. The developer guide has the code.", "connections/developers", "Open the guide"],
            ].map(([head, tail, path, action], i) => (
              <li key={head} className="flex flex-col gap-2 @2xl:flex-row @2xl:items-center @2xl:gap-4">
                <span className="num flex size-7 flex-none items-center justify-center rounded-full border border-border text-meta font-medium text-ink">{i + 1}</span>
                <span className="min-w-0 flex-1"><span className="block text-body font-medium text-ink">{head}</span><span className="block text-body text-muted">{tail}</span></span>
                <Button asChild variant={i === 0 ? "primary" : "secondary"}><Link href={href(path)}>{action}</Link></Button>
              </li>
            ))}
          </ol>
        </Panel>
      ) : (
        <>
          <section aria-label="How documents move" className="flex flex-col gap-2 @3xl:flex-row @3xl:items-stretch">
            <Stage
              title="Receive" href={href("connections/receive")} figure={o.receive.accepted_24h} caption="documents received today"
              lines={[["Channels", `${o.receive.active} of ${o.receive.channels} on`], ["Refused today", o.receive.rejected_24h, o.receive.rejected_24h > 0]]}
            />
            <Connector />
            <Stage
              title="Syruss reads" href={href("review")} figure={settling} caption={settling === 1 ? "thing being settled" : "things being settled"}
              lines={[["Waiting for a review", workspace.needs_review], ["Questions and conversations", workspace.open_questions + (workspace.open_conversations ?? 0)]]}
            />
            <Connector />
            <Stage
              title="Publish" href={href("connections/deliveries")} figure={o.publish.delivered_24h} caption="records delivered today" tone={o.publish.dead > 0 ? "problem" : undefined}
              lines={[["Waiting to send", o.publish.waiting], ["Held back", o.publish.held], ["Failed", o.publish.dead, o.publish.dead > 0]]}
            />
          </section>

          <p className="flex items-center gap-2 text-body text-muted">
            <span className={cn("size-2 flex-none rounded-full", o.state === "ok" ? "bg-[var(--positive)]" : o.state === "attention" ? "bg-[var(--pill-warning-ink)]" : "bg-[var(--negative)]")} aria-hidden />
            {state.text}
          </p>

          {o.problems.length > 0 && (
            <Panel title="Needs attention">
              <ul className="flex flex-col">
                {o.problems.map((p, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-3 border-b border-rule py-2.5 first:pt-0 last:border-0 last:pb-0">
                    <span className="min-w-0 flex-1"><span className="block text-body font-medium text-ink">{p.title}</span>{p.detail && <span className="block line-clamp-2 text-meta text-muted">{p.detail}</span>}</span>
                    <Button asChild variant="secondary" size="sm">
                      <Link href={p.kind === "channel_paused" ? href(`connections/receive/${p.slug}`) : p.kind === "destination_paused" ? href(`connections/publish/${p.destination_id}`) : href("connections/deliveries?filter=failed")}>
                        {p.kind === "delivery_dead" ? "Look into it" : "Open"}
                      </Link>
                    </Button>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel
            title="Held back" aside={o.held.length > 0 ? <Link className="hover:text-ink" href={href("connections/deliveries?filter=held")}>See all {o.publish.held}</Link> : undefined}
          >
            {o.held.length === 0 ? (
              <p className="flex items-center gap-2 text-body text-muted"><Check className="size-4 text-[var(--positive)]" /> Nothing is held back. Everything approved has gone out or is on its way.</p>
            ) : (
              <>
                <p className="mb-3 text-body text-muted">These are approved, but something is still open about them, so they are not sent. They go out by themselves once it is settled.</p>
                <ul className="flex flex-col">
                  {o.held.map((h) => (
                    <li key={h.delivery_id} className="flex flex-wrap items-start gap-x-4 gap-y-1 border-b border-rule py-2.5 first:pt-0 last:border-0 last:pb-0">
                      <span className="min-w-0 flex-1">
                        <Link href={href(`documents/${h.document.id}`)} className="block truncate text-body font-medium text-ink hover:underline">{h.document.filename}</Link>
                        <span className="block text-meta text-muted">{h.reason} · for {h.destination}</span>
                      </span>
                      <span className="num text-meta text-muted">{ago(h.since)}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
