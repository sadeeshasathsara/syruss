"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ObjectIcon } from "@/components/objects/ObjectIcon";
import { ProfileHeader, Stats, value, WeekBars } from "@/components/objects/Profile";
import { RecordsTable, ReferencedRecordsTable } from "@/components/objects/Records";
import { Bar, ErrorNote, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, dateTime, humanize } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

/** One thing's profile: its attributes as last read, when it was met, what it appears with and its documents. */
export default function ThingProfilePage() {
  const { type: rawType, id: rawId } = useParams<{ type: string; id: string }>();
  const type = decodeURIComponent(rawType);
  const id = decodeURIComponent(rawId);
  const { workspace, href } = useWorkspace();
  const profile = useQuery({
    queryKey: ["thing", workspace.id, type, id],
    queryFn: () => api.objects.thing(workspace.id, type, id),
    placeholderData: keepPreviousData,
  });
  const objectHref = href(`objects/${encodeURIComponent(type)}`);

  if (profile.isError) return <ErrorNote message={(profile.error as Error).message} onRetry={() => profile.refetch()} />;
  const p = profile.data;
  if (!p) return <div className="flex flex-col gap-4"><Bar className="h-20 w-full" /><Bar className="h-20 w-full" /><Bar className="h-64 w-full" /></div>;

  return (
    <div className="flex flex-col gap-6">
      <ProfileHeader
        icon={p.object.icon}
        kind={<Link href={objectHref} className="hover:text-ink">{p.object.type}</Link>}
        title={p.thing.label}
        back={{ href: objectHref, label: `All ${p.object.type}` }}
        facts={
          <>
            {p.thing.label !== p.thing.id && <Pill>{p.thing.id}</Pill>}
            {p.knowledge?.aliases.map((a) => <Pill key={a} tone="info">also “{a}”</Pill>)}
          </>
        }
      />
      <Stats
        items={[
          { label: "Documents", value: p.stats.documents },
          { label: "First seen", value: p.stats.first_seen ? ago(p.stats.first_seen) : "—" },
          { label: "Last seen", value: p.stats.last_seen ? ago(p.stats.last_seen) : "—" },
          { label: "Appears with", value: p.appears_with.length },
        ]}
      />

      <div className="grid grid-cols-1 gap-4 @5xl:grid-cols-[3fr_2fr]">
        <div className="flex min-w-0 flex-col gap-4">
          <Panel title="Attributes" aside={p.stats.last_seen ? `As last read, ${dateTime(p.stats.last_seen)}` : undefined}>
            <dl className="grid grid-cols-1 gap-x-8 @3xl:grid-cols-2">
              {p.attributes.map((a) => (
                <div key={a.field} className="flex min-w-0 flex-col gap-0.5 border-t border-rule py-2">
                  <dt className="text-meta text-muted">{humanize(a.field)}</dt>
                  <dd className="text-body text-ink [overflow-wrap:anywhere]">{value(a.value)}</dd>
                  {a.other_values.length > 0 && (
                    <dd className="text-meta text-muted">Also read as {a.other_values.map((v) => `“${v}”`).join(", ")}</dd>
                  )}
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title="Mentions per week">
            <WeekBars weeks={p.weeks} what="documents mentioning it" />
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Panel title="Appears with">
            {p.appears_with.length ? (
              <ul className="flex flex-col">
                {p.appears_with.map((n) => (
                  <li key={n.key} className="border-t border-rule first:border-0">
                    <Link href={href(n.path)} className="flex min-w-0 items-center gap-3 py-2 hover:bg-raised">
                      <ObjectIcon name={n.icon} size="sm" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body text-ink">{n.label}</span>
                        <span className="block text-meta text-muted">{n.type}</span>
                      </span>
                      <span className="num flex-none text-meta text-muted">{n.count}×</span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-body text-muted">Nothing yet: it hasn&apos;t been on a document with another identified thing.</p>
            )}
          </Panel>
          {p.knowledge && (
            <Panel title="What the workspace knows" aside={<Link href={href("knowledge")} className="text-ink underline-offset-2 hover:underline">Knowledge</Link>}>
              <dl className="flex flex-col gap-2 text-body">
                <div>
                  <dt className="text-meta text-muted">Known as</dt>
                  <dd className="text-ink">{p.knowledge.name}</dd>
                </div>
                {Object.entries(p.knowledge.attributes).map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-meta text-muted">{humanize(k)}</dt>
                    <dd className="text-ink">{value(v)}</dd>
                  </div>
                ))}
                <p className="text-meta text-muted">
                  Added by {p.knowledge.created_by === "agent" ? "the agent" : p.knowledge.created_by === "import" ? "a data source" : "a person"}
                </p>
              </dl>
            </Panel>
          )}
        </div>
      </div>

      <RecordsTable type={p.object.type} thing={p.thing.id} title="Its records" />
      {p.referenced_by?.map((ref) => (
        <ReferencedRecordsTable
          key={`${ref.type}.${ref.field}`}
          objType={p.object.type}
          thingId={p.thing.id}
          refType={ref.type}
          refField={ref.field}
        />
      ))}
    </div>
  );
}
