"use client";

import Link from "next/link";
import { Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { useAccess } from "@/lib/access";
import { useWorkspace } from "@/lib/workspace";
import { useOverview } from "./Overview";

const STATE = { ok: { tone: "positive", label: "All good" }, attention: { tone: "warning", label: "Needs a look" }, problem: { tone: "negative", label: "Needs attention" } } as const;

/** A small summary of the workspace's connections for its overview. Shown only to people who manage them, and only once something is connected. */
export function ConnectionsHealth() {
  const { workspace, href } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const allowed = can("integrations.manage");
  const overview = useOverview(allowed);
  const o = overview.data;
  if (!allowed || !o || (o.receive.channels === 0 && o.publish.destinations === 0)) return null;
  const state = STATE[o.state];
  return (
    <Panel title="Connections" aside={<Link href={href("connections")} className="hover:text-ink">Open</Link>}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2"><Pill tone={state.tone}>{state.label}</Pill></div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-body @2xl:grid-cols-4">
          <Fig label="Received today" value={o.receive.accepted_24h} />
          <Fig label="Delivered today" value={o.publish.delivered_24h} />
          <Fig label="Held back" value={o.publish.held} href={o.publish.held ? href("connections/deliveries?filter=held") : undefined} />
          <Fig label="Could not be sent" value={o.publish.dead} bad={o.publish.dead > 0} href={o.publish.dead ? href("connections/deliveries?filter=failed") : undefined} />
        </dl>
      </div>
    </Panel>
  );
}

function Fig({ label, value, bad, href }: { label: string; value: number; bad?: boolean; href?: string }) {
  const body = (
    <>
      <dt className="text-meta text-muted">{label}</dt>
      <dd className={`num mt-0.5 text-section font-semibold ${bad ? "text-negative" : "text-ink"}`}>{value}</dd>
    </>
  );
  return href ? <Link href={href} className="block rounded-control hover:bg-raised">{body}</Link> : <div>{body}</div>;
}
