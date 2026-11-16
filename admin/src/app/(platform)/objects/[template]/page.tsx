"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { ObjectIcon } from "@/components/objects/ObjectIcon";
import { WhereTags } from "@/components/objects/Where";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

/** A workspace's objects, each opening to its profile. */
export default function WorkspaceObjectsPage({ params }: { params: Promise<{ template: string }> }) {
  const { template } = use(params);
  const data = useQuery({ queryKey: ["objects", template], queryFn: () => api.objects.list(template) });
  if (data.isError) return <ErrorNote message={(data.error as Error).message} onRetry={() => data.refetch()} />;
  const d = data.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={d ? `Objects in ${d.workspace.name}` : "Objects"} back={d ? { href: `/accounts/organizations/${d.organization.id}`, label: d.organization.name } : undefined}
        subtitle={d ? <span className="flex flex-wrap gap-2"><WhereTags where={d} /></span> : " "} />
      {!d ? <div className="grid grid-cols-1 gap-4 @3xl:grid-cols-2">{[0, 1].map((i) => <Bar key={i} className="h-32 w-full" />)}</div>
        : !d.objects.length ? <Empty title="No objects">This workspace has no published template yet.</Empty>
        : (
          <ul className="grid grid-cols-1 gap-4 @3xl:grid-cols-2 @6xl:grid-cols-3">
            {d.objects.map((o) => (
              <li key={o.type}>
                <Link href={`/objects/${template}/${encodeURIComponent(o.type)}`}
                  className="flex h-full flex-col gap-3 rounded-card bg-card p-4 [box-shadow:var(--shadow-card)] hover:bg-raised">
                  <span className="flex items-center gap-3">
                    <ObjectIcon name={o.icon} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-section font-semibold text-ink">{o.type}</span>
                      <span className="block text-meta text-muted">{o.multiple ? "Many per document" : "One per document"} · {o.fields} attributes</span>
                    </span>
                    {o.identity && <Pill tone="accent">Has an identity</Pill>}
                  </span>
                  {o.description && <span className="line-clamp-2 text-body text-muted">{o.description}</span>}
                  <span className="mt-auto flex flex-wrap gap-x-4 text-meta text-muted">
                    <span className="num"><b className="font-medium text-ink">{o.documents}</b> documents</span>
                    {o.identity && <span className="num"><b className="font-medium text-ink">{o.things}</b> met</span>}
                    {o.last_seen && <span>last {ago(o.last_seen)}</span>}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}
