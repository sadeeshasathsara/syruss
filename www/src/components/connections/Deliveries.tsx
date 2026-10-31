"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Search } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useDeferredValue, useState } from "react";
import { NoAccess, SettingsHeader } from "@/components/settings/kit";
import { Bar, Empty, ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Sheet, SheetClose } from "@/components/ui/Sheet";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import type { DeliveryRow } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { DELIVERY_FILTERS, DELIVERY_STATUS, inTime, KIND_NOUN } from "./delivery-status";

function useRefresh() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  return () => ["deliveries", "destinations", "connections"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, workspace.id] }));
}

function DeliveryDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { workspace, href } = useWorkspace();
  const refresh = useRefresh();
  const detail = useQuery({ queryKey: ["deliveries", workspace.id, "one", id], queryFn: () => api.deliveries.get(workspace.id, id!), enabled: !!id });
  const act = useMutation({
    mutationFn: (what: "replay" | "release" | "cancel") => api.deliveries[what](workspace.id, id!),
    onSuccess: refresh,
  });
  const d = detail.data;
  const meta = d ? DELIVERY_STATUS[d.status] : null;
  return (
    <Sheet open={!!id} onOpenChange={(open) => !open && onClose()} side="right" title="Delivery" className="max-w-[580px]">
      <div className="flex items-center justify-between px-4 pt-3">
        <span className="text-meta text-muted">{d?.destination ? `To ${d.destination.name}` : ""}</span>
        <SheetClose />
      </div>
      <div className="scrollbar-thin flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 pb-6 pt-2">
        {detail.isLoading && <Bar className="h-24 w-full" />}
        {detail.isError && <ErrorNote message="Couldn’t load this delivery." onRetry={() => detail.refetch()} />}
        {d && meta && (
          <>
            <div>
              <h2 className="break-words text-section font-semibold text-ink">{d.document?.filename ?? "Document"}</h2>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Pill tone={meta.tone}>{meta.label}</Pill>
                {d.document?.is_test && <Pill tone="info">Test document</Pill>}
                <Pill>{d.event === "document.corrected" ? `Correction · revision ${d.revision}` : "First approval"}</Pill>
              </div>
            </div>

            {d.status === "held" && d.hold_reason && (
              <p className="rounded-card bg-raised px-4 py-3 text-body text-ink">
                <span className="font-medium">Held back. </span>{d.hold_reason} It is sent by itself once that is settled.
              </p>
            )}
            {(d.status === "dead" || d.status === "retrying" || d.status === "cancelled") && d.last_error && (
              <p className="rounded-card bg-raised px-4 py-3 text-body text-negative">{d.last_error}</p>
            )}
            {d.status === "retrying" && <p className="text-body text-muted">Next try {inTime(d.next_attempt_at)}.</p>}

            <div className="flex flex-wrap gap-2">
              {d.status === "held" && <Button variant="primary" disabled={act.isPending} onClick={() => act.mutate("release")}>Send anyway</Button>}
              {(d.status === "dead" || d.status === "cancelled" || d.status === "delivered") && (
                <Button variant={d.status === "delivered" ? "secondary" : "primary"} disabled={act.isPending} onClick={() => act.mutate("replay")}>
                  <RotateCcw /> {d.status === "delivered" ? "Send again" : "Replay"}
                </Button>
              )}
              {["held", "pending", "retrying", "dead"].includes(d.status) && <Button variant="ghost" disabled={act.isPending} onClick={() => act.mutate("cancel")}>Cancel</Button>}
              {d.document && <Button asChild variant="ghost"><Link href={href(`documents/${d.document.id}`)}>Open the document</Link></Button>}
            </div>
            {act.error && <p className="text-body text-negative">{act.error.message}</p>}

            <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-5 gap-y-2 text-body">
              <Fact label="Destination" value={d.destination ? `${d.destination.name} (${KIND_NOUN[d.destination.kind] ?? d.destination.kind})` : "Removed"} />
              {d.document?.external_id && <Fact label="Your reference" value={d.document.external_id} mono />}
              <Fact label="Tries" value={String(d.attempts)} />
              {d.delivered_at && <Fact label="Delivered" value={dateTime(d.delivered_at)} />}
              {d.external_ref && <Fact label="Their reference" value={d.external_ref} mono />}
              <Fact label="Idempotency key" value={d.idempotency_key} mono />
            </dl>

            {d.message_problems.length > 0 && (
              <div className="rounded-card bg-raised px-4 py-3 text-body">
                <p className="font-medium text-ink">The mapping can’t be applied to this record</p>
                <ul className="mt-1 list-disc pl-5 text-negative">{d.message_problems.map((p) => <li key={p}>{p}</li>)}</ul>
              </div>
            )}
            {d.message && (
              <section className="flex flex-col gap-2">
                <h3 className="text-body font-medium text-ink">What is sent</h3>
                <CodeBlock samples={[{ label: "JSON", code: JSON.stringify(d.message, null, 2) }]} />
              </section>
            )}

            <section className="flex flex-col gap-2">
              <h3 className="text-body font-medium text-ink">Tries</h3>
              {d.attempt_log.length === 0 ? <p className="text-body text-muted">Not tried yet.</p> : (
                <ul className="flex flex-col rounded-card border border-border bg-card">
                  {d.attempt_log.map((a) => (
                    <li key={`${a.no}-${a.at}`} className="border-b border-rule px-4 py-2.5 last:border-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={a.ok ? "positive" : "negative"}>{a.ok ? "Accepted" : "Failed"}{a.status_code ? ` · ${a.status_code}` : ""}</Pill>
                        <span className="text-body text-ink">Try {a.no}</span>
                        <span className="num ml-auto text-meta text-muted">{a.duration_ms != null ? `${Math.round(a.duration_ms)} ms · ` : ""}{dateTime(a.at)}</span>
                      </div>
                      {a.error && <p className="mt-1 text-body text-negative">{a.error}</p>}
                      {(a.request || a.response) && (
                        <details className="mt-1.5">
                          <summary className="cursor-pointer text-meta text-muted">Request and reply</summary>
                          {a.request && <pre className="scrollbar-thin mt-1.5 max-h-48 overflow-auto rounded-control bg-raised p-3 font-mono text-meta text-ink">{a.request}</pre>}
                          {a.response && <pre className="scrollbar-thin mt-1.5 max-h-48 overflow-auto rounded-control bg-raised p-3 font-mono text-meta text-ink">{a.response}</pre>}
                        </details>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </Sheet>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className={cn("min-w-0 break-words text-ink", mono && "font-mono text-meta")}>{value}</dd>
    </>
  );
}

function Row({ d, onOpen }: { d: DeliveryRow; onOpen: () => void }) {
  const meta = DELIVERY_STATUS[d.status];
  const note = d.status === "held" ? d.hold_reason : d.status === "retrying" ? `${d.last_error ?? "Failed"} Next try ${inTime(d.next_attempt_at)}.` : d.status === "dead" || d.status === "cancelled" ? d.last_error : null;
  return (
    <li className="border-b border-rule last:border-0">
      <button type="button" onClick={onOpen} className="flex w-full flex-col gap-1 px-4 py-3 text-left transition-colors hover:bg-raised @2xl:flex-row @2xl:items-center @2xl:gap-4">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-body font-medium text-ink">{d.document?.filename ?? "Document"}</span>
            {d.document?.is_test && <Pill tone="info">Test</Pill>}
            {d.event === "document.corrected" && <Pill>Correction</Pill>}
          </span>
          <span className="block truncate text-meta text-muted">
            {d.destination?.name ?? "Removed destination"}{d.document?.external_id ? ` · ${d.document.external_id}` : ""}
          </span>
          {note && <span className={cn("mt-0.5 block line-clamp-2 text-meta", d.status === "held" ? "text-muted" : "text-negative")}>{note}</span>}
        </span>
        <span className="flex flex-none items-center gap-3">
          <Pill tone={meta.tone}>{meta.label}</Pill>
          <span className="num w-20 text-right text-meta text-muted">{d.delivered_at ? ago(d.delivered_at) : ago(d.updated_at)}</span>
        </span>
      </button>
    </li>
  );
}

export function DeliveriesPage() {
  const { workspace } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const allowed = can("integrations.manage");
  const refresh = useRefresh();
  const params = useSearchParams();
  const [filter, setFilter] = useState(DELIVERY_FILTERS.some((f) => f.key === params.get("filter")) ? params.get("filter")! : "all");
  const [destination, setDestination] = useState(params.get("destination") ?? "all");
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search);
  const [open, setOpen] = useState<string | null>(null);
  const statuses = DELIVERY_FILTERS.find((f) => f.key === filter)!.statuses;
  const stats = useQuery({ queryKey: ["deliveries", workspace.id, "stats"], queryFn: () => api.deliveries.stats(workspace.id), enabled: allowed });
  const destinations = useQuery({ queryKey: ["destinations", workspace.id], queryFn: () => api.destinations.list(workspace.id), enabled: allowed });
  const list = useInfiniteQuery({
    queryKey: ["deliveries", workspace.id, "list", filter, destination, q],
    queryFn: ({ pageParam }) => api.deliveries.list(workspace.id, { status: statuses, destinationId: destination === "all" ? undefined : destination, q, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => { const seen = all.reduce((n, p) => n + p.items.length, 0); return seen < last.total ? seen : undefined; },
    enabled: allowed,
  });
  const replayAll = useMutation({ mutationFn: () => api.deliveries.replayMany(workspace.id, { status: "dead", destination_id: destination === "all" ? undefined : destination }), onSuccess: refresh });
  const title = "Deliveries";
  const description = "Every approved record on its way out: where it is, why it is waiting, and what happened on each try.";
  if (!allowed) return <div className="flex flex-col"><SettingsHeader title={title} description={description} /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;

  const count = (statusList: string[]) => statusList.length === 0 ? Object.values(stats.data?.by_status ?? {}).reduce((a, b) => a + b, 0) : statusList.reduce((n, s) => n + (stats.data?.by_status[s as keyof typeof stats.data.by_status] ?? 0), 0);
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const failed = stats.data?.by_status.dead ?? 0;

  return (
    <div className="flex flex-col">
      <SettingsHeader
        title={title}
        description={description}
        actions={failed > 0 && filter !== "delivered" ? (
          <Button variant="secondary" disabled={replayAll.isPending} onClick={() => replayAll.mutate()}><RotateCcw /> Replay {failed} failed</Button>
        ) : undefined}
      />
      <div className="flex flex-col gap-3 pt-5">
        <div className="flex flex-col gap-2 @3xl:flex-row @3xl:items-center">
          <div role="tablist" aria-label="Show" className="scrollbar-thin -mx-4 flex gap-1 overflow-x-auto px-4 @3xl:mx-0 @3xl:flex-1 @3xl:px-0">
            {DELIVERY_FILTERS.map((f) => (
              <button
                key={f.key} role="tab" type="button" aria-selected={filter === f.key} onClick={() => setFilter(f.key)}
                className={cn("inline-flex h-8 flex-none items-center gap-1.5 rounded-control px-2.5 text-body transition-colors pointer-coarse:h-10", filter === f.key ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}
              >
                {f.label}<span className="num text-meta text-muted">{count(f.statuses)}</span>
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <div className="w-44">
              <Select label="Destination" value={destination} onChange={setDestination} options={[{ value: "all", label: "All destinations" }, ...(destinations.data ?? []).map((d) => ({ value: d.id, label: d.name }))]} />
            </div>
            <div className="relative min-w-0 flex-1 @3xl:w-56 @3xl:flex-none">
              <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <Input aria-label="Search deliveries" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="File, reference or error" className="pl-8" />
            </div>
          </div>
        </div>

        {list.isLoading ? <div className="flex flex-col gap-2">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-14 w-full" />)}</div> : list.isError ? (
          <ErrorNote message="Couldn’t load the deliveries." onRetry={() => list.refetch()} />
        ) : items.length === 0 ? (
          <Empty title={filter === "all" && !q ? "Nothing has been sent yet" : "No deliveries match"}>
            {filter === "all" && !q ? "When a document is approved, a delivery to each of your destinations appears here." : "Try another filter or search."}
          </Empty>
        ) : (
          <ul className="@container rounded-card border border-border bg-card">{items.map((d) => <Row key={d.id} d={d} onOpen={() => setOpen(d.id)} />)}</ul>
        )}
        {list.hasNextPage && <div><Button variant="secondary" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>{list.isFetchingNextPage ? "Loading…" : "Show more"}</Button></div>}
      </div>
      <DeliveryDrawer id={open} onClose={() => setOpen(null)} />
    </div>
  );
}
