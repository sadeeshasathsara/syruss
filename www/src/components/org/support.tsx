"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { NoAccess, SavedNote, Section, SettingRow, SettingsHeader } from "@/components/settings/kit";
import { Bar, Empty } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Switch } from "@/components/ui/Switch";
import { api, type SupportAccess } from "@/lib/api";
import { dateTime } from "@/lib/format";

const key = (org: string | undefined) => ["support-access", org];
const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/**
 * Whether Syruss support may open this organization's content, who has it open now, and when it was opened before.
 * Owners and admins decide, and end an open access at any time.
 */
export function SupportAccessSettings() {
  const { org } = useParams<{ org: string }>();
  const queryClient = useQueryClient();
  const access = useQuery({ queryKey: key(org), queryFn: api.supportAccess.get });
  const done = (next: SupportAccess) => queryClient.setQueryData(key(org), next);
  const allow = useMutation({ mutationFn: (allowed: boolean) => api.supportAccess.set(allowed), onSuccess: done });
  const end = useMutation({ mutationFn: (id: string) => api.supportAccess.end(id), onSuccess: done });
  const a = access.data;

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Support access"
        description="When you ask Syruss for help, support may need to see what you see. They open your content only for a reason and a while, read only, and you're told each time." />
      <Section title="Allow support to open your content"
        description="Each time it's for a stated reason, for at most four hours, and every document and run they open is written down. Turned off, support sees only facts (what ran, when, how it ended), never your content.">
        {!a ? <Bar className="h-9 w-full" /> : (
          <div className="flex flex-col gap-3">
            <SettingRow label="Syruss support may open this organization's content" htmlFor="support-access"
              help={a.allowed ? "On: owners and admins are emailed whenever it's opened." : "Off: support can't open your content. Turning it off ended any open access."}
              control={<Switch id="support-access" label="Allow Syruss support" checked={a.allowed} disabled={!a.can_manage || allow.isPending} onChange={(v) => allow.mutate(v)} />} />
            <SavedNote pending={allow.isPending} error={allow.error} saved={false} />
            {!a.can_manage && <NoAccess what="change support access" role={undefined} />}
          </div>
        )}
      </Section>
      <Section title="Open now" description="Ending it closes their view at once.">
        {!a ? <Bar className="h-9 w-full" /> : !a.open.length ? <p className="text-body text-muted">Nobody from Syruss support has your content open.</p> : (
          <ul className="flex flex-col gap-2">
            {a.open.map((g) => (
              <li key={g.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-card bg-[var(--pill-warning-bg)] px-4 py-3 text-[var(--pill-warning-ink)]">
                <Eye className="size-4 flex-none" aria-hidden />
                <span className="min-w-0 flex-1 text-body">
                  <span className="font-medium">{g.staff}</span> until {time(g.until)}. <span className="opacity-80">Why: {g.reason}</span>
                </span>
                {a.can_manage && <Button size="sm" variant="danger" onClick={() => end.mutate(g.id)} disabled={end.isPending}>End access now</Button>}
              </li>
            ))}
            {end.error && <p className="text-body text-negative">{end.error.message}</p>}
          </ul>
        )}
      </Section>
      <Section title="Before" description="The last 20 times support opened your content.">
        {!a ? <Bar className="h-9 w-full" /> : !a.recent.length ? <Empty title="Never">Syruss support hasn&apos;t opened your content.</Empty> : (
          <ul className="flex flex-col divide-y divide-rule">
            {a.recent.map((g) => (
              <li key={g.id} className="flex flex-col gap-0.5 py-2.5">
                <p className="flex flex-wrap items-center gap-2 text-body text-ink">
                  <span className="font-medium">{g.staff}</span>
                  <span className="text-muted">{dateTime(g.opened_at)}</span>
                  {g.open ? <Pill tone="warning">Open</Pill> : g.ended_by ? <Pill>Ended by {g.ended_by}</Pill> : <Pill>Ran out</Pill>}
                </p>
                <p className="text-meta text-muted">{g.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

/** At the top of every page while Syruss support has the organization's content open, for everyone in it. */
export function SupportAccessBanner() {
  const { org } = useParams<{ org: string }>();
  const access = useQuery({ queryKey: key(org), queryFn: api.supportAccess.get, refetchInterval: 60_000, retry: false, enabled: !!org });
  const open = access.data?.open ?? [];
  if (!open.length) return null;
  const first = open[0];
  return (
    <div role="status" className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-card bg-[var(--pill-warning-bg)] px-4 py-3 text-body text-[var(--pill-warning-ink)]">
      <Eye className="size-4 flex-none" aria-hidden />
      <p className="min-w-0 flex-1">
        <span className="font-medium">Syruss support can see this organization&apos;s content</span> ({first.staff}, until {time(first.until)}
        {open.length > 1 ? `, and ${open.length - 1} more` : ""}). Why: {first.reason}
      </p>
      <Link href={`/${org}/settings/organization/support`} className="font-medium underline underline-offset-2">
        {access.data?.can_manage ? "Review or end it" : "Details"}
      </Link>
    </div>
  );
}
