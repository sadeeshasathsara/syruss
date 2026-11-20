"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Figures, ProfileHeader, Stats, ThingList, TYPE_LABEL, WeekBars } from "@/components/objects/Profile";
import { RecordsTable } from "@/components/objects/Records";
import { Bar, ErrorNote, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, humanize } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

/** One object's profile: its picture and attributes, its figures, its things and the documents it is on. */
export default function ObjectProfilePage() {
  const { type: raw } = useParams<{ type: string }>();
  const type = decodeURIComponent(raw);
  const { workspace, href } = useWorkspace();
  const [q, setQ] = useState("");
  const profile = useQuery({
    queryKey: ["object", workspace.id, type],
    queryFn: () => api.objects.get(workspace.id, type),
    placeholderData: keepPreviousData,
  });
  const found = useQuery({
    queryKey: ["object-things", workspace.id, type, q],
    queryFn: () => api.objects.things(workspace.id, type, q),
    enabled: !!profile.data?.object.identity && q.trim().length > 0,
    placeholderData: keepPreviousData,
  });
  const objectHref = (t: string) => href(`objects/${encodeURIComponent(t)}`);

  if (profile.isError) return <ErrorNote message={(profile.error as Error).message} onRetry={() => profile.refetch()} />;
  const p = profile.data;
  if (!p) return <div className="flex flex-col gap-4"><Bar className="h-20 w-full" /><Bar className="h-20 w-full" /><Bar className="h-64 w-full" /></div>;
  const o = p.object;
  const shown = q.trim() ? found.data?.items : p.things?.items;

  return (
    <div className="flex flex-col gap-6">
      <ProfileHeader
        icon={o.icon}
        kind="Object"
        title={o.type}
        description={o.description}
        back={{ href: href("objects"), label: "All objects" }}
        facts={
          <>
            <Pill>{o.multiple ? "Many per document" : "One per document"}</Pill>
            {o.identity && <Pill tone="accent">Identified by {humanize(o.identity.id_field)}, named by {humanize(o.identity.label_field)}</Pill>}
            {!o.icon_chosen && <span className="text-meta text-muted">No icon chosen yet: pick one in the template.</span>}
          </>
        }
      />
      <Stats
        items={[
          { label: "Documents", value: p.stats.documents },
          o.identity ? { label: "Met so far", value: p.stats.things } : { label: "Rows read", value: p.stats.rows },
          { label: "Last seen", value: p.stats.last_seen ? ago(p.stats.last_seen) : "—" },
          { label: "Attributes", value: o.fields.length },
        ]}
      />

      <div className="grid grid-cols-1 gap-4 @5xl:grid-cols-[3fr_2fr]">
        <div className="flex min-w-0 flex-col gap-4">
          {o.identity && p.things && (
            <Panel title={`${o.type} met so far`} aside={<span className="num">{p.stats.things}</span>}>
              <label className="mb-2 flex h-8 items-center gap-2 rounded-control border border-control bg-card px-2.5 focus-within:border-ink">
                <Search className="size-4 flex-none text-muted" aria-hidden />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Find one by name or identifier"
                  aria-label={`Find a ${o.type}`}
                  className="min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-faint"
                />
              </label>
              {shown ? <ThingList things={shown} icon={o.icon} href={(t) => href(t.path)} /> : <Bar className="h-24 w-full" />}
            </Panel>
          )}
          <Panel title="Documents per week">
            <WeekBars weeks={p.weeks} what="documents with it" />
          </Panel>
          <Panel title="Its attributes in figures" aside={p.stats.records_capped ? `From the newest ${p.stats.records_read} records` : `${p.stats.rows} rows`}>
            <Figures figures={p.figures} />
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Panel title="Attributes">
            <dl className="flex flex-col">
              {o.fields.map((f) => {
                const isIdentity = o.identity && [o.identity.id_field, o.identity.label_field].includes(f.name);
                return (
                  <div key={f.name} className="flex min-w-0 flex-col gap-0.5 border-t border-rule py-2 first:border-0">
                    <dt className="flex flex-wrap items-center gap-2 text-body text-ink">
                      {humanize(f.name)}
                      {f.required && <span className="text-meta text-muted">required</span>}
                      {isIdentity && <Pill tone="accent">identity</Pill>}
                      <span className="ml-auto text-meta text-muted">{TYPE_LABEL[f.type] ?? f.type}</span>
                    </dt>
                    {f.resolves_to && (
                      <dd className="text-meta text-muted">
                        Points to <Link href={objectHref(f.resolves_to)} className="text-ink underline-offset-2 hover:underline">{f.resolves_to}</Link>
                      </dd>
                    )}
                    {f.description && <dd className="text-meta text-muted">{f.description}</dd>}
                  </div>
                );
              })}
            </dl>
          </Panel>


          {(p.referenced_by.length > 0 || Object.keys(p.links).length > 0) && (
            <Panel title="Connected objects">
              <ul className="flex flex-col gap-2 text-body text-muted">
                {Object.entries(p.links).map(([field, target]) => (
                  <li key={field}>
                    Its {humanize(field)} points to{" "}
                    <Link href={objectHref(target)} className="font-medium text-ink underline-offset-2 hover:underline">{target}</Link>
                  </li>
                ))}
                {p.referenced_by.map((r) => (
                  <li key={`${r.type}.${r.field}`}>
                    <Link href={objectHref(r.type)} className="font-medium text-ink underline-offset-2 hover:underline">{r.type}</Link> points here with its{" "}
                    {humanize(r.field)}
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {p.screens.length > 0 && (
            <Panel title="Analytics on it">
              <ul className="flex flex-col gap-1.5">
                {p.screens.map((s) => (
                  <li key={s.slug}>
                    <Link href={href(`analytics/${s.slug}`)} className="text-body text-ink underline-offset-2 hover:underline">{s.name}</Link>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>

      {o.identity && <RecordsTable type={o.type} title={`${o.type} records`} />}
    </div>
  );
}
