"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { ApiSourceForm } from "@/components/knowledge/ApiSourceForm";
import { MappingSummary } from "@/components/knowledge/MappingSummary";
import { MemoryView } from "@/components/knowledge/MemoryView";
import { useShell } from "@/components/shell/ShellContext";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { api } from "@/lib/api";
import { ago, type Tone } from "@/lib/format";
import type { KnowledgeSource, MappingPreview, SourceMapping } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const SOURCE: Record<string, string> = { human: "Added by your team", import: "Imported", agent: "Learned by the agent" };

const SOURCE_STATUS: Record<KnowledgeSource["status"], { label: string; tone: Tone }> = {
  MAPPING: { label: "Agent is mapping", tone: "info" },
  REVIEW: { label: "Mapping to review", tone: "warning" },
  READY: { label: "Imported", tone: "positive" },
  ERROR: { label: "Couldn't read", tone: "negative" },
};

/** Upload a CSV or JSON file; the agent works out how it maps. */
function FilePanel({ onDone }: { onDone: () => void }) {
  const { workspace } = useWorkspace();
  const { announce } = useShell();
  const queryClient = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [chosen, setChosen] = useState<File | null>(null);
  const [notes, setNotes] = useState("");

  const add = useMutation({
    mutationFn: () => api.sources.addFile(workspace.id, chosen!, notes),
    onSuccess: (source) => {
      announce(`You added '${source.name}'. The agent is working out how it maps.`, { openChat: true });
      queryClient.invalidateQueries({ queryKey: ["sources", workspace.id] });
      onDone();
    },
  });

  return (
    <Panel title="Upload a file">
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (chosen) add.mutate();
        }}
      >
        <div className="flex flex-col gap-2">
          <span className="text-body font-medium text-ink">CSV or JSON file</span>
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="secondary" onClick={() => file.current?.click()}>Choose file</Button>
            <span className="min-w-0 truncate text-body text-muted">{chosen?.name ?? "No file chosen"}</span>
          </div>
          <input ref={file} type="file" accept=".csv,.json" className="sr-only" onChange={(e) => setChosen(e.target.files?.[0] ?? null)} />
        </div>
        <Field label="Notes for the agent (optional)" help="What this list is and anything the column names don't say.">
          {(id) => <Textarea id={id} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} />}
        </Field>
        {add.error && <p className="text-body text-negative">{add.error.message}</p>}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={!chosen || add.isPending}>{add.isPending ? "Uploading…" : "Upload and map"}</Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}

/** One source: where it stands, its mapping (to approve, or as approved) and what you can do. */
function SourceCard({ source, onEdit }: { source: KnowledgeSource; onEdit: () => void }) {
  const { workspace } = useWorkspace();
  const { announce } = useShell();
  const queryClient = useQueryClient();
  const wid = workspace.id;
  const [showMapping, setShowMapping] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const proposals = useQuery({ queryKey: ["proposals", wid, "PENDING"], queryFn: () => api.proposals.list(wid) });
  const pending = proposals.data?.find((p) => p.proposal_type === "SOURCE_MAPPING" && p.proposed_data.source_id === source.id);
  const refresh = () => ["sources", "entities", "proposals", "thread"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, wid] }));

  const decide = useMutation({
    mutationFn: (action: "APPROVE" | "REJECT") => api.proposals.review(wid, pending!.id, action),
    onSuccess: (_, action) => {
      if (action === "APPROVE") announce(`You approved the mapping for '${source.name}'. Its records are imported.`);
      refresh();
    },
  });
  const sync = useMutation({ mutationFn: () => api.sources.sync(wid, source.id), onSettled: refresh });
  const remap = useMutation({ mutationFn: () => api.sources.remap(wid, source.id, ""), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.sources.remove(wid, source.id), onSuccess: refresh });
  const status = SOURCE_STATUS[source.status];
  const error = decide.error ?? sync.error ?? remap.error ?? remove.error;

  return (
    <article className="animate-rise rounded-card border border-border bg-card">
      <header className="flex flex-wrap items-start gap-x-3 gap-y-1 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="flex flex-wrap items-center gap-2 text-section font-semibold text-ink">
            <span className="min-w-0 break-all">{source.name}</span>
            <Pill tone={status.tone}>{status.label}</Pill>
          </h3>
          <p className="text-body text-muted">
            {source.kind === "API" ? `API, ${source.api?.method} ${source.api?.url}` : "File"}
            {source.status === "READY" && `, ${source.imported_count} record${source.imported_count === 1 ? "" : "s"}`}
            {source.last_synced_at && `, read ${ago(source.last_synced_at).toLowerCase()}`}
          </p>
          {source.status === "ERROR" && source.last_error && <p className="mt-1 text-body text-negative">{source.last_error}</p>}
          {source.status === "MAPPING" && (
            <p className="mt-1 text-body text-muted">The agent is reading the records and working out what they are. Follow along in the chat.</p>
          )}
        </div>
        <div className="flex flex-none flex-wrap items-center gap-1">
          {source.kind === "API" && source.mapping && (
            <Button size="sm" variant="secondary" disabled={sync.isPending} onClick={() => sync.mutate()}>
              {sync.isPending ? "Syncing…" : "Sync now"}
            </Button>
          )}
          {source.kind === "API" && <Button size="sm" variant="ghost" onClick={onEdit}>Settings</Button>}
          {source.status !== "MAPPING" && (
            <Button size="sm" variant="ghost" disabled={remap.isPending} onClick={() => remap.mutate()}>Map again</Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setConfirmRemove(true)}>Remove</Button>
        </div>
      </header>

      {pending ? (
        <div className="flex flex-col gap-3 border-t border-border px-4 py-3">
          <p className="text-body text-ink">{pending.observation}</p>
          <MappingSummary
            mapping={pending.proposed_data.mapping as SourceMapping}
            preview={pending.proposed_data.preview as MappingPreview[]}
          />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate("APPROVE")}>
              {decide.isPending ? "Importing…" : "Approve and import"}
            </Button>
            <Button variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate("REJECT")}>Reject</Button>
          </div>
        </div>
      ) : (
        source.mapping && (
          <div className="border-t border-border px-4 py-2">
            <Button size="sm" variant="ghost" aria-expanded={showMapping} onClick={() => setShowMapping((s) => !s)}>
              {showMapping ? "Hide the mapping" : "How it maps"}
            </Button>
            <div className="collapsible" data-open={showMapping}>
              <div>
                <div className="pb-2 pt-1">
                  <MappingSummary mapping={source.mapping} />
                </div>
              </div>
            </div>
          </div>
        )
      )}
      {sync.data?.imported && (
        <p className="border-t border-border px-4 py-2 text-body text-muted">
          Synced: {sync.data.imported.created} new, {sync.data.imported.updated} updated.
        </p>
      )}
      {error && <p className="border-t border-border px-4 py-2 text-body text-negative">{error.message}</p>}

      <Dialog
        open={confirmRemove}
        onOpenChange={(open) => !remove.isPending && setConfirmRemove(open)}
        title={`Remove ${source.name}?`}
        description="The source and its saved credentials are deleted. People and things it already imported stay known."
      >
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmRemove(false)}>Keep it</Button>
          <Button variant="primary" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending ? "Removing…" : "Remove the source"}
          </Button>
        </div>
      </Dialog>
    </article>
  );
}

export default function Knowledge() {
  const { workspace, href } = useWorkspace();
  const tab = useSearchParams().get("tab");
  const onSources = tab === "sources";
  const entities = useQuery({ queryKey: ["entities", workspace.id], queryFn: () => api.entities.list(workspace.id) });
  const sources = useQuery({ queryKey: ["sources", workspace.id], queryFn: () => api.sources.list(workspace.id) });
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState<"file" | "api" | null>(null);
  const [editing, setEditing] = useState<KnowledgeSource | null>(null);
  const [type, setType] = useState<string | null>(null);

  const all = useMemo(() => entities.data ?? [], [entities.data]);
  const types = useMemo(() => [...new Set(all.map((e) => e.entity_type))].sort(), [all]);
  const rows = all.filter(
    (e) =>
      (!type || e.entity_type === type) &&
      (!query.trim() || [e.name, e.identifier_value, ...e.aliases].some((v) => v?.toLowerCase().includes(query.trim().toLowerCase()))),
  );
  const addActions = (
    <>
      <Button variant="secondary" onClick={() => { setEditing(null); setAdding("file"); }}>Upload a file</Button>
      <Button variant="secondary" onClick={() => { setEditing(null); setAdding("api"); }}>Connect an API</Button>
    </>
  );

  if (tab === "memory") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          title="What Syruss remembers"
          subtitle="What it has learned about this business, and about you. Correct it, pin what matters, or make it forget."
        />
        <MemoryView />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={onSources ? "Data sources" : "Knowledge"}
        subtitle={
          onSources
            ? "The lists you already keep, from files or straight from the systems that hold them. The agent works out how each maps; you approve the import."
            : "The people and things the agent recognises on your documents, and the other ways they're written."
        }
        actions={onSources && !adding && !editing && addActions}
      />
      {onSources && adding === "file" && <FilePanel onDone={() => setAdding(null)} />}
      {onSources && adding === "api" && <ApiSourceForm onDone={() => setAdding(null)} />}
      {onSources && editing && <ApiSourceForm key={editing.id} existing={editing} onDone={() => setEditing(null)} />}

      {onSources && sources.data?.length === 0 && !adding && (
        <Empty title="No data sources yet" action={<div className="flex flex-wrap gap-2">{addActions}</div>}>
          Upload a CSV or JSON file, or connect the API of the system that keeps your staff, sites or suppliers.
        </Empty>
      )}
      {onSources && (sources.data?.length ?? 0) > 0 && (
        <section aria-label="Data sources" className="flex flex-col gap-2">
          <div className="flex flex-col gap-3 rounded-sheet bg-well p-2 sm:p-3">
            {sources.data!.map((s) => (
              <SourceCard key={s.id} source={s} onEdit={() => { setAdding(null); setEditing(s); }} />
            ))}
          </div>
        </section>
      )}
      {onSources && sources.isError && <ErrorNote message="Couldn't load the data sources." onRetry={() => sources.refetch()} />}

      {!onSources && (
      <>
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <span className="sr-only">Search</span>
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search names, IDs and spellings" className="pl-9" />
        </label>
        {types.length > 1 && (
          <Select
            label="Type"
            value={type ?? "all"}
            onChange={(v) => setType(v === "all" ? null : v)}
            options={[{ value: "all", label: "All types" }, ...types.map((t) => ({ value: t, label: t }))]}
            className="w-auto min-w-40"
          />
        )}
        <span className="num ml-auto text-body text-muted">{rows.length} of {all.length}</span>
      </div>

      {entities.isError && <ErrorNote message="Couldn't load knowledge." onRetry={() => entities.refetch()} />}
      {entities.isLoading ? (
        <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
      ) : all.length === 0 ? (
        <Empty title="Nothing known yet" action={<Button asChild variant="secondary"><Link href={href("knowledge?tab=sources")}>Add a data source</Link></Button>}>
          Add your staff, sites or suppliers, from a file or straight from the system that keeps them, so the agent can recognise
          them even when a name is hard to read. It also adds what it learns from reviews and your answers.
        </Empty>
      ) : (
        <table className="w-full table-fixed border-collapse text-body">
          <thead className="sr-only @2xl:not-sr-only">
            <tr className="text-left text-meta text-muted">
              <th className="py-2 pr-3 font-normal">Name</th>
              <th className="w-32 py-2 pr-3 font-normal">ID</th>
              <th className="w-40 py-2 pr-3 font-normal">Type</th>
              <th className="w-48 py-2 font-normal">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} className="border-t border-rule align-top">
                <td className="py-2.5 pr-3">
                  <span className="block truncate text-ink">{e.name}</span>
                  {e.aliases.length > 0 && <span className="block truncate text-meta text-muted">Also written {e.aliases.map((a) => `“${a}”`).join(", ")}</span>}
                  <span className="mt-0.5 block text-meta text-muted @2xl:hidden">
                    {[e.entity_type, e.identifier_value].filter(Boolean).join(", ")}
                  </span>
                </td>
                <td className="hidden py-2.5 pr-3 font-mono text-meta text-ink @2xl:table-cell">{e.identifier_value ?? <span className="text-faint">—</span>}</td>
                <td className="hidden py-2.5 pr-3 text-muted @2xl:table-cell">{e.entity_type}</td>
                <td className="hidden py-2.5 text-muted @2xl:table-cell">{SOURCE[e.created_by] ?? e.created_by}, {ago(e.created_at).toLowerCase()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </>
      )}
    </div>
  );
}
