"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";

function Count({ label, value, alert }: { label: string; value: number; alert?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]">
      <span className="truncate text-body text-muted">{label}</span>
      <span className={cn("num text-figure", alert ? "text-negative" : value ? "text-ink" : "text-faint")}>{value.toLocaleString()}</span>
    </div>
  );
}

/** Signs of trouble across the platform, and a review of who can use these tools. */
export default function SecurityPage() {
  const view = useQuery({ queryKey: ["console", "security"], queryFn: api.console.security, refetchInterval: 60_000 });
  if (view.isError) return <ErrorNote message={view.error.message} onRetry={() => view.refetch()} />;
  const v = view.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Security" subtitle="Accounts under attack, staff with customers' content open, and who is on the staff. The last seven days unless it says otherwise." />
      {!v ? <Bar className="h-64 w-full" /> : (
        <>
          <section className="grid grid-cols-2 gap-3 @4xl:grid-cols-4" aria-label="The last seven days">
            <Count label="Wrong sign-in codes" value={v.last_7_days.wrong_codes} alert={v.wrong_code_bursts.length > 0} />
            <Count label="Codes sent" value={v.last_7_days.codes_sent} />
            <Count label="Browsers confirmed" value={v.last_7_days.browsers_confirmed} />
            <Count label="Browsers removed" value={v.last_7_days.browsers_removed} />
          </section>

          <Panel title="Many wrong codes in a day" aside={<span className="text-meta text-muted">5 or more in 24 hours</span>}>
            {!v.wrong_code_bursts.length ? <p className="text-body text-muted">None: nobody is guessing at an account&apos;s codes.</p> : (
              <ul className="flex flex-col divide-y divide-rule">
                {v.wrong_code_bursts.map((b) => (
                  <li key={b.user_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                    <Link href={`/accounts/people/${b.user_id}`} className="min-w-0 flex-1 truncate text-body text-ink hover:underline">{b.name} <span className="text-muted">{b.email}</span></Link>
                    <Pill tone="negative">{b.count} wrong codes</Pill>
                    <span className="num text-meta text-muted">last {ago(b.last_at).toLowerCase()}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Customers' content open now" aside={<span className="num">{v.open_access.length}</span>}>
            {!v.open_access.length ? <p className="text-body text-muted">No staff member has any organization&apos;s content open.</p> : (
              <ul className="flex flex-col divide-y divide-rule">
                {v.open_access.map((g, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-x-3 py-2 text-body">
                    <span className="font-medium text-ink">{g.staff_name}</span>
                    <Link href={`/accounts/organizations/${g.tenant_id}`} className="text-ink hover:underline">{g.organization}</Link>
                    <span className="min-w-0 flex-1 text-muted">{g.reason}</span>
                    <span className="num text-meta text-muted">until {new Date(g.expires_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Staff access review" aside={<Link href="/staff" className="hover:text-ink">Manage the staff</Link>} bodyClassName="p-0">
            <p className="px-4 pt-3 text-meta text-muted">Remove anyone who no longer needs these tools. Not seen for 60 days is marked.</p>
            <ul className="flex flex-col divide-y divide-rule">
              {v.staff_review.map((s) => (
                <li key={s.user_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
                  <Link href={`/accounts/people/${s.user_id}`} className="min-w-0 flex-1 truncate text-body text-ink hover:underline">{s.name} <span className="text-muted">{s.email}</span></Link>
                  <Pill tone="accent">{s.role}</Pill>
                  {s.stale && <Pill tone="warning">Not seen for 60 days</Pill>}
                  {s.status !== "active" && <Pill tone="negative">{s.status}</Pill>}
                  <span className="num text-meta text-muted">
                    {s.last_seen_at ? `seen ${ago(s.last_seen_at).toLowerCase()}` : "never seen"}{s.last_action_at ? ` · last change ${ago(s.last_action_at).toLowerCase()}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        </>
      )}
    </div>
  );
}
