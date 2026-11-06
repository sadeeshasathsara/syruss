"use client";

import { useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Meter, Throughput, compact, duration, money } from "@/components/ui/Figures";
import { Pill } from "@/components/ui/Pill";
import { OrgFrame } from "@/components/shell/OrgFrame";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { ago, cn, percent, type Tone } from "@/lib/format";
import type { OrgOverview, OrgPeriod, OrgPeriodTotals, OrgWorkspaceHealth } from "@/lib/types";

const PERIODS: OrgPeriod[] = [7, 30, 90];
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export default function OrganizationOverview() {
  const { org } = useParams<{ org: string }>();
  const { canOrg } = useMe();
  const [days, setDays] = useState<OrgPeriod>(30);
  const overview = useQuery({ queryKey: ["org-overview", days], queryFn: () => api.org.overview(days), placeholderData: (previous) => previous });
  const o = overview.data;

  const waiting = o ? [
    o.attention.needs_review && `${plural(o.attention.needs_review, "document")} to review`,
    o.attention.questions + o.attention.threads && `${plural(o.attention.questions + o.attention.threads, "question")} from Syruss`,
    o.attention.failed && `${plural(o.attention.failed, "failed document")}`,
    o.attention.drafts && `${plural(o.attention.drafts, "workspace")} still in design`,
  ].filter(Boolean) : [];
  const settingUp = o && canOrg("workspaces.create") && !Object.values(o.setup).every(Boolean);

  return (
    <OrgFrame org={org} title="Overview">
      <div className="flex flex-col gap-6">
        <PageHeader
          title={o?.organization.name ?? "Overview"}
          subtitle={o ? (waiting.length ? `Waiting on you: ${waiting.join(", ")}.` : "Nothing is waiting on you.") : "Your organization at a glance."}
          actions={
            <>
              <PeriodPicker value={days} onChange={setDays} />
              {canOrg("org.members.manage") && <Button asChild variant="secondary"><Link href={`/${org}/people`}>Invite people</Link></Button>}
              {canOrg("workspaces.create") && <Button asChild variant="primary"><Link href={`/${org}/new`}>New workspace</Link></Button>}
            </>
          }
        />
        {overview.isError && <ErrorNote message="Couldn't load the overview." onRetry={() => overview.refetch()} />}

        {settingUp && <GetStarted org={org} setup={o.setup} />}

        <Headline o={o} />

        <Panel title="Documents received" aside={o ? `${dayRange(o.period.from, o.period.to)}` : undefined}>
          {o ? <Throughput days={o.period.series} /> : <Bar className="h-40 w-full" />}
        </Panel>

        <div className="grid grid-cols-1 items-start gap-6 @4xl:grid-cols-3">
          <NeedsYou org={org} o={o} className="@4xl:col-span-2" />
          <ThisMonth org={org} o={o} canManage={canOrg("org.members.manage")} />
        </div>

        <WorkspaceHealth org={org} o={o} days={days} canCreate={canOrg("workspaces.create")} />

        <div className={cn("grid grid-cols-1 items-start gap-6", o?.activity !== null && "@4xl:grid-cols-2")}>
          <LatestWork org={org} o={o} />
          {o?.activity !== null && (
            <Panel title="Organization activity" aside={<Link href={`/${org}/activity`} className="hover:text-ink">Open activity</Link>}>
              {!o ? (
                <Rows />
              ) : (o.activity ?? []).length === 0 ? (
                <p className="text-body text-muted">Changes to people, roles and workspaces appear here.</p>
              ) : (
                <ul className="flex flex-col divide-y divide-rule">
                  {o.activity!.map((a) => (
                    <li key={a.id} className="flex items-baseline gap-3 py-2">
                      <span className="min-w-0 flex-1 text-body text-ink">{a.text}</span>
                      <span className="num flex-none text-meta text-muted">{ago(a.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          )}
        </div>
      </div>
    </OrgFrame>
  );
}

function Rows({ n = 3 }: { n?: number }) {
  return <div className="flex flex-col gap-3">{Array.from({ length: n }, (_, i) => <Bar key={i} className="h-5 w-full" />)}</div>;
}

function dayRange(from: string, to: string): string {
  const f = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return `${f(from)} – ${f(to)}`;
}

function PeriodPicker({ value, onChange }: { value: OrgPeriod; onChange: (d: OrgPeriod) => void }) {
  return (
    <div className="flex items-center gap-0.5 rounded-control border border-border p-0.5" role="group" aria-label="Period">
      {PERIODS.map((d) => (
        <button
          key={d}
          type="button"
          aria-pressed={value === d}
          onClick={() => onChange(d)}
          className={cn("num h-7 rounded-chip px-2.5 text-body transition-colors", value === d ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}
        >
          {d} days
        </button>
      ))}
    </div>
  );
}

const STEPS: { key: keyof OrgOverview["setup"]; title: string; note: string; href: (org: string) => string }[] = [
  { key: "workspace", title: "Create a workspace", note: "One workspace holds one kind of document.", href: (org) => `/${org}/new` },
  { key: "published", title: "Finish its template", note: "Upload a few samples; Syruss proposes what to capture.", href: (org) => `/${org}/workspaces` },
  { key: "documents", title: "Send the first documents", note: "Upload them, or connect a channel they arrive through.", href: (org) => `/${org}/workspaces` },
  { key: "people", title: "Invite your team", note: "Reviewers and admins see what waits on them.", href: (org) => `/${org}/people` },
];

function GetStarted({ org, setup }: { org: string; setup: OrgOverview["setup"] }) {
  const done = STEPS.filter((s) => setup[s.key]).length;
  return (
    <Panel title="Get started" aside={<span className="num">{done} of {STEPS.length} done</span>}>
      <ol className="grid grid-cols-1 gap-3 @3xl:grid-cols-4">
        {STEPS.map((s, i) => (
          <li key={s.key}>
            <Link href={s.href(org)} className={cn("flex h-full gap-3 rounded-card border border-border px-3 py-3 transition-colors hover:bg-raised", setup[s.key] && "opacity-70")}>
              <span
                aria-hidden
                className={cn("num flex size-6 flex-none items-center justify-center rounded-full text-meta", setup[s.key] ? "bg-positive text-[var(--pill-positive-bg)]" : "bg-active text-ink")}
              >
                {setup[s.key] ? <Check className="size-3.5" /> : i + 1}
              </span>
              <span className="min-w-0">
                <span className={cn("block text-body font-medium text-ink", setup[s.key] && "line-through decoration-faint")}>{s.title}</span>
                <span className="block text-meta text-muted">{setup[s.key] ? "Done" : s.note}</span>
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

/** How a figure moved since the period before: up is good for some figures, bad for others. */
function Change({ now, before, kind, better }: { now: number | null; before: number | null; kind: "count" | "rate" | "seconds" | "money"; better: "up" | "down" | "none" }) {
  if (now === null || before === null || (kind !== "rate" && before === 0)) return <span className="text-meta text-muted">No earlier figure to compare</span>;
  const diff = kind === "rate" ? Math.round((now - before) * 100) : Math.round(((now - before) / before) * 100);
  if (diff === 0) return <span className="text-meta text-muted">Same as the period before</span>;
  const good = better === "none" ? null : (diff > 0) === (better === "up");
  return (
    <span className={cn("num text-meta", good === null ? "text-muted" : good ? "text-positive" : "text-negative")}>
      {diff > 0 ? "▲" : "▼"} {Math.abs(diff)}{kind === "rate" ? " pts" : "%"} <span className="text-muted">vs the period before</span>
    </span>
  );
}

/** One figure in the headline strip; `i` places the rules between figures (two columns, then four). */
function Tile({ i, label, value, children }: { i: number; label: string; value?: string; children?: React.ReactNode }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1 border-border px-4 py-3", i > 0 && "@3xl:border-l", i % 2 === 1 && "border-l", i >= 2 && "border-t @3xl:border-t-0")}>
      <span className="truncate text-body text-muted">{label}</span>
      {value === undefined ? <Bar className="h-7 w-16" /> : <span className={cn("num text-figure", value === "—" ? "text-faint" : "text-ink")}>{value}</span>}
      {value !== undefined && children}
    </div>
  );
}

function Headline({ o }: { o?: OrgOverview }) {
  const c: Partial<OrgPeriodTotals> = o?.period.current ?? {};
  const p: Partial<OrgPeriodTotals> = o?.period.previous ?? {};
  const priced = c.cost_usd != null;
  return (
    <section aria-label="How work went in this period" className="grid grid-cols-2 overflow-hidden rounded-card bg-card [box-shadow:var(--shadow-card)] @3xl:grid-cols-4">
      <Tile i={0} label="Documents received" value={o ? c.received!.toLocaleString() : undefined}>
        <Change now={c.received ?? null} before={p.received ?? null} kind="count" better="none" />
      </Tile>
      <Tile i={1} label="Saved without a person" value={o ? percent(c.hands_free_rate) : undefined}>
        <Change now={c.hands_free_rate ?? null} before={p.hands_free_rate ?? null} kind="rate" better="up" />
      </Tile>
      <Tile i={2} label="Time to save" value={o ? (c.avg_seconds_to_save == null ? "—" : duration(c.avg_seconds_to_save)) : undefined}>
        <Change now={c.avg_seconds_to_save ?? null} before={p.avg_seconds_to_save ?? null} kind="seconds" better="down" />
      </Tile>
      {o?.costs ? (
        // A model with no price set records tokens only: show those rather than a cost of nothing.
        <Tile i={3} label={priced ? "Agent cost" : "Agent tokens"} value={priced ? money(c.cost_usd!) : compact(c.tokens ?? 0)}>
          {priced || !c.runs
            ? <Change now={c.cost_usd ?? null} before={p.cost_usd ?? null} kind="money" better="none" />
            : <span className="text-meta text-muted">No price is set for the model, so no cost</span>}
        </Tile>
      ) : (
        <Tile i={3} label="Failed" value={o ? percent(c.failure_rate) : undefined}>
          <Change now={c.failure_rate ?? null} before={p.failure_rate ?? null} kind="rate" better="down" />
        </Tile>
      )}
    </section>
  );
}

function NeedsYou({ org, o, className }: { org: string; o?: OrgOverview; className?: string }) {
  const items: { key: string; href: string; text: string; tone: Tone; tag: string; workspace: string }[] = [];
  for (const w of o?.needs_you ?? []) {
    const base = `/${org}/w/${w.slug}`;
    const add = (kind: string, href: string, text: string, tone: Tone, tag: string) => items.push({ key: `${w.slug}-${kind}`, href, text, tone, tag, workspace: w.name });
    if (w.status === "draft") add("setup", `${base}/setup`, "Finish designing the template", "neutral", "Setup");
    if (w.needs_review) add("review", `${base}/review`, `${plural(w.needs_review, "document")} to review`, "warning", "Review");
    if (w.questions + w.threads) add("ask", `${base}/agent`, `${plural(w.questions + w.threads, "question")} from Syruss`, "info", "Question");
    if (w.failed) add("failed", `${base}/documents?view=failed`, `${plural(w.failed, "document")} failed`, "negative", "Failed");
  }
  return (
    <Panel title="Needs you" aside={items.length ? <span className="num">{items.length}</span> : undefined} className={className}>
      {!o ? (
        <Rows />
      ) : items.length === 0 ? (
        <p className="text-body text-muted">Nothing is waiting on you in your workspaces.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-rule">
          {items.slice(0, 8).map((i) => (
            <li key={i.key}>
              <Link href={i.href} className="flex items-center gap-3 py-2.5 hover:underline">
                <Pill tone={i.tone} className="w-16 justify-center">{i.tag}</Pill>
                <span className="min-w-0 flex-1 truncate text-body text-ink">{i.text}</span>
                <span className="max-w-[40%] flex-none truncate text-meta text-muted">{i.workspace}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function ThisMonth({ org, o, canManage }: { org: string; o?: OrgOverview; canManage: boolean }) {
  const month = new Date().toLocaleDateString(undefined, { month: "long" });
  return (
    <Panel title="This month" aside={month}>
      {!o ? (
        <Rows n={4} />
      ) : (
        <div className="flex flex-col gap-4">
          <dl className="grid grid-cols-2 gap-3">
            <div>
              <dt className="text-meta text-muted">Documents</dt>
              <dd className="num text-section font-semibold text-ink">{o.this_month.documents.toLocaleString()}</dd>
            </div>
            {o.costs && (
              <div>
                <dt className="text-meta text-muted">{o.this_month.cost_usd === null ? "Agent tokens" : "Agent cost"}</dt>
                <dd className="num text-section font-semibold text-ink">
                  {o.this_month.cost_usd === null ? compact(o.this_month.tokens ?? 0) : money(o.this_month.cost_usd)}
                </dd>
              </div>
            )}
          </dl>
          {o.this_month.near_limit.length > 0 && (
            <p className="rounded-control bg-[var(--pill-warning-bg)] px-3 py-2 text-meta text-[var(--pill-warning-ink)]">
              Near this month&apos;s document limit: {o.this_month.near_limit.join(", ")}.
            </p>
          )}
          <ul className="flex flex-col divide-y divide-rule border-t border-rule text-body">
            <Line label="Workspaces" value={o.workspaces.total} note={o.workspaces.draft ? `${o.workspaces.draft} in design` : undefined} href={`/${org}/workspaces`} />
            <Line label="People" value={o.people.total} note={`${plural(o.people.owners, "owner")}, ${plural(o.people.admins, "admin")}`} href={`/${org}/people`} />
            {canManage && o.pending_invitations !== null && <Line label="Invitations waiting" value={o.pending_invitations} href={`/${org}/people`} />}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function Line({ label, value, note, href }: { label: string; value: number; note?: string; href: string }) {
  return (
    <li>
      <Link href={href} className="flex items-baseline gap-3 py-2 hover:underline">
        <span className="flex-1 text-muted">{label}</span>
        {note && <span className="text-meta text-muted">{note}</span>}
        <span className="num text-ink">{value}</span>
      </Link>
    </li>
  );
}

/** One word and a colour for how a workspace is doing, worst first. */
function health(w: OrgWorkspaceHealth): { tone: string; label: string } {
  if (w.status === "draft") return { tone: "bg-faint", label: "In design" };
  if (w.failed_now || (w.documents_limit && w.documents_this_month >= 0.9 * w.documents_limit)) return { tone: "bg-negative", label: "Needs attention" };
  if (w.needs_review + w.open_questions + w.open_threads) return { tone: "bg-[var(--pill-warning-ink)]", label: "Waiting on people" };
  if (!w.last_activity_at || Date.now() - new Date(w.last_activity_at).getTime() > 7 * 86400000) return { tone: "bg-faint", label: "Quiet" };
  return { tone: "bg-positive", label: "Healthy" };
}

function WorkspaceHealth({ org, o, days, canCreate }: { org: string; o?: OrgOverview; days: OrgPeriod; canCreate: boolean }) {
  const rows = o?.workspace_health ?? [];
  const cost = !!o?.costs;
  return (
    <Panel title="Workspaces" aside={<Link href={`/${org}/workspaces`} className="hover:text-ink">See all</Link>} bodyClassName="px-0 pb-1">
      {!o ? (
        <div className="px-4 pb-3"><Rows /></div>
      ) : rows.length === 0 ? (
        <div className="px-4 pb-3">
          <Empty
            title={canCreate ? "Create your first workspace" : "You're not in a workspace yet"}
            action={canCreate ? <Button asChild variant="primary" size="sm"><Link href={`/${org}/new`}>New workspace</Link></Button> : undefined}
          >
            {canCreate
              ? "A workspace holds one kind of document. Upload a few samples and Syruss proposes what to capture."
              : "Ask someone who runs a workspace to invite you."}
          </Empty>
        </div>
      ) : (
        <div className="overflow-x-auto scrollbar-thin">
          <table className="w-full min-w-[720px] text-body">
            <thead>
              <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
                <th scope="col">Workspace</th>
                <th scope="col" className="text-right">Received <span className="num">({days} d)</span></th>
                <th scope="col" className="text-right">Without a person</th>
                <th scope="col" className="text-right">Waiting</th>
                <th scope="col" className="text-right">Failed</th>
                <th scope="col" className="w-44">This month</th>
                {cost && <th scope="col" className="text-right">Cost <span className="num">({days} d)</span></th>}
                <th scope="col" className="text-right">Last activity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {rows.map((w) => {
                const h = health(w);
                const waiting = w.needs_review + w.open_questions + w.open_threads;
                const href = w.my_role ? `/${org}/w/${w.slug}${w.status === "draft" ? "/setup" : ""}` : `/${org}/workspaces`;
                return (
                  <tr key={w.id} className="hover:bg-raised [&>td]:px-4 [&>td]:py-2.5">
                    <td className="max-w-64">
                      <Link href={href} className="flex min-w-0 items-center gap-2.5 hover:underline">
                        <span aria-hidden title={h.label} className={cn("size-2 flex-none rounded-full", h.tone)} />
                        <span className="min-w-0 truncate text-ink">{w.name}</span>
                        <span className="sr-only">{h.label}</span>
                        {!w.my_role && <Pill className="flex-none">Not a member</Pill>}
                      </Link>
                    </td>
                    <td className="num text-right text-ink">{w.received.toLocaleString()}</td>
                    <td className="num text-right text-ink">{percent(w.hands_free_rate)}</td>
                    <td className={cn("num text-right", waiting ? "text-ink" : "text-faint")}>{waiting}</td>
                    <td className={cn("num text-right", w.failed_now ? "text-negative" : "text-faint")}>{w.failed_now}</td>
                    <td><Meter label={w.plan} used={w.documents_this_month} limit={w.documents_limit} /></td>
                    {cost && (
                      <td className="num text-right text-ink" title={w.cost_usd === null ? "No price is set for the model: tokens used" : undefined}>
                        {w.cost_usd === null ? `${compact(w.tokens ?? 0)} tokens` : money(w.cost_usd)}
                      </td>
                    )}
                    <td className="num text-right text-muted">{w.last_activity_at ? ago(w.last_activity_at) : "Never"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

const LEVEL_TONE: Record<OrgOverview["latest_work"][number]["level"], string> = {
  info: "bg-faint", success: "bg-positive", warning: "bg-[var(--pill-warning-ink)]", error: "bg-negative",
};

function LatestWork({ org, o }: { org: string; o?: OrgOverview }) {
  return (
    <Panel title="Latest work">
      {!o ? (
        <Rows n={4} />
      ) : o.latest_work.length === 0 ? (
        <p className="text-body text-muted">Each step Syruss takes with a document in your workspaces appears here.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-rule">
          {o.latest_work.map((e) => (
            <li key={e.id}>
              <Link
                href={`/${org}/w/${e.workspace_slug}/${e.document_id ? `documents/${e.document_id}` : "log"}`}
                className="flex items-baseline gap-3 py-2 hover:underline"
              >
                <span aria-hidden className={cn("size-2 flex-none translate-y-[-1px] rounded-full", LEVEL_TONE[e.level])} />
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-2 text-body text-ink">{e.message}</span>
                  <span className="block truncate text-meta text-muted">{e.workspace}</span>
                </span>
                <span className="num flex-none text-meta text-muted">{ago(e.created_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
