"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ObjectIcon } from "@/components/objects/ObjectIcon";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

/** Every object of the template, each opening to its own page. */
export default function ObjectsPage() {
  const { workspace, href } = useWorkspace();
  const objects = useQuery({ queryKey: ["objects", workspace.id], queryFn: () => api.objects.list(workspace.id) });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Objects"
        subtitle="The people, places and things your documents name. Open one to see each one met so far, its figures and its records."
      />
      {objects.isError ? (
        <ErrorNote message={(objects.error as Error).message} onRetry={() => objects.refetch()} />
      ) : !objects.data ? (
        <div className="grid grid-cols-1 gap-4 @3xl:grid-cols-2 @6xl:grid-cols-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-36 w-full" />)}</div>
      ) : !objects.data.some((o) => o.identity) ? (
        <Empty title="Nothing identified yet">Objects with an identity, such as an employee or a location, appear here once the template names what identifies them.</Empty>
      ) : (
        <ul className="grid grid-cols-1 gap-4 @3xl:grid-cols-2 @6xl:grid-cols-3">
          {objects.data.filter((o) => o.identity).map((o) => (
            <li key={o.type}>
              <Link
                href={href(`objects/${encodeURIComponent(o.type)}`)}
                className="flex h-full flex-col gap-3 rounded-card bg-card p-4 [box-shadow:var(--shadow-card)] hover:bg-raised"
              >
                <span className="flex items-center gap-3">
                  <ObjectIcon name={o.icon} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-section font-semibold text-ink">{o.type}</span>
                    <span className="block text-meta text-muted">
                      {o.multiple ? "Many per document" : "One per document"} · {o.fields} attribute{o.fields === 1 ? "" : "s"}
                    </span>
                  </span>
                  {o.identity && <Pill tone="accent">Has an identity</Pill>}
                </span>
                {o.description && <span className="line-clamp-2 text-body text-muted">{o.description}</span>}
                <span className="mt-auto flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted">
                  <span className="num"><b className="font-medium text-ink">{o.documents}</b> document{o.documents === 1 ? "" : "s"}</span>
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
