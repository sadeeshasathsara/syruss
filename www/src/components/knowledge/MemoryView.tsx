"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Pencil, Pin, PinOff, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { Bar, Empty, ErrorNote } from "@/components/ui/Blocks";
import { Button, IconButton } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { api } from "@/lib/api";
import { ago, cn, type Tone } from "@/lib/format";
import type { MemoryItem, MemoryKind } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { SystemLessons } from "./SystemLessons";

const KIND: Record<MemoryKind, { label: string; tone: Tone }> = {
  lesson: { label: "Lesson", tone: "accent" },
  observation: { label: "Observation", tone: "neutral" },
  fact: { label: "Fact", tone: "info" },
  preference: { label: "Preference", tone: "positive" },
  summary: { label: "Summary", tone: "neutral" },
};
const SOURCE: Record<string, string> = {
  run: "Learned while working",
  conversation: "From a chat",
  person: "Taught by a person",
  import: "From earlier notes",
};

function MemoryRow({ m, editable, onEdit }: { m: MemoryItem; editable: boolean; onEdit: (m: MemoryItem) => void }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["memories", workspace.id] });
  const update = useMutation({ mutationFn: (patch: { pinned?: boolean; archived?: boolean }) => api.memories.update(workspace.id, m.id, patch), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.memories.remove(workspace.id, m.id), onSuccess: refresh });
  return (
    <li className="group flex animate-rise gap-3 border-b border-rule px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <p className="text-body text-ink">{m.content}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted">
          <Pill tone={KIND[m.kind].tone}>{KIND[m.kind].label}</Pill>
          {m.pinned && <Pill tone="warning">Pinned</Pill>}
          <span>{SOURCE[m.source?.type ?? ""] ?? "Remembered"}, {ago(m.created_at).toLowerCase()}</span>
          {m.use_count > 0 && <span>used {m.use_count} time{m.use_count === 1 ? "" : "s"}{m.last_used_at ? `, last ${ago(m.last_used_at).toLowerCase()}` : ""}</span>}
        </p>
      </div>
      {editable && (
        <div className="flex flex-none items-start gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
          <IconButton aria-label={m.pinned ? "Unpin" : "Pin: always use it"} onClick={() => update.mutate({ pinned: !m.pinned })}>
            {m.pinned ? <PinOff /> : <Pin />}
          </IconButton>
          <IconButton aria-label="Edit" onClick={() => onEdit(m)}><Pencil /></IconButton>
          {m.scope === "workspace" && (
            <IconButton aria-label="Archive: no longer true" onClick={() => update.mutate({ archived: true })}><Archive /></IconButton>
          )}
          <IconButton aria-label="Forget this" onClick={() => remove.mutate()}><Trash2 /></IconButton>
        </div>
      )}
    </li>
  );
}

function Teach({ scope }: { scope: "personal" | "workspace" }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const kinds: MemoryKind[] = scope === "personal" ? ["preference", "fact"] : ["lesson", "fact", "observation"];
  const [kind, setKind] = useState<MemoryKind>(kinds[0]);
  const [content, setContent] = useState("");
  const teach = useMutation({
    mutationFn: () => api.memories.teach(workspace.id, { scope, kind, content: content.trim() }),
    onSuccess: () => {
      setContent("");
      queryClient.invalidateQueries({ queryKey: ["memories", workspace.id] });
    },
  });
  return (
    <form
      className="flex flex-col gap-2 @2xl:flex-row @2xl:items-start"
      onSubmit={(e) => {
        e.preventDefault();
        if (content.trim().length >= 3) teach.mutate();
      }}
    >
      <Select label="Kind" value={kind} onChange={setKind} options={kinds.map((k) => ({ value: k, label: KIND[k].label }))} className="@2xl:w-40" />
      <Input
        aria-label="What Syruss should remember"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        maxLength={2000}
        placeholder={scope === "personal" ? "e.g. I prefer short answers with a table" : "e.g. Night shifts run from 19:00 to 07:00"}
        className="flex-1"
      />
      <Button type="submit" variant="secondary" disabled={content.trim().length < 3 || teach.isPending}>Remember this</Button>
      {teach.error && <p className="text-body text-negative">{teach.error.message}</p>}
    </form>
  );
}

function Scope({ scope }: { scope: "personal" | "workspace" }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("all");
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [text, setText] = useState("");
  const [confirmForget, setConfirmForget] = useState(false);
  const list = useQuery({
    queryKey: ["memories", workspace.id, scope, kind, q],
    queryFn: () => api.memories.list(workspace.id, scope, { kind: kind === "all" ? undefined : kind, q: q.trim() || undefined }),
  });
  const save = useMutation({
    mutationFn: () => api.memories.update(workspace.id, editing!.id, { content: text.trim() }),
    onSuccess: () => {
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ["memories", workspace.id] });
    },
  });
  const forgetMe = useMutation({
    mutationFn: () => api.memories.forgetMe(workspace.id),
    onSuccess: () => {
      setConfirmForget(false);
      queryClient.invalidateQueries({ queryKey: ["memories", workspace.id] });
    },
  });
  const editable = scope === "personal" || !!list.data?.can_manage_workspace;
  const items = list.data?.items ?? [];
  const kinds = scope === "personal" ? ["preference", "fact"] : ["lesson", "observation", "fact"];

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-section font-semibold text-ink">
            {scope === "personal" ? "About you" : "About this workspace"}
            {list.data && <span className="num ml-2 text-body font-normal text-muted">{list.data.counts[scope]}</span>}
          </h2>
          <p className="text-body text-muted">
            {scope === "personal"
              ? "Only you can see this. Syruss uses it only when it's talking with you, in this workspace."
              : "How this business works, shared by everyone here. Syruss uses it in all its work."}
          </p>
        </div>
        {scope === "personal" && items.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setConfirmForget(true)}>Forget everything about me</Button>
        )}
      </div>

      {editable && <Teach scope={scope} />}

      <div className="flex flex-wrap items-center gap-2">
        <label className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <span className="sr-only">Search</span>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" className="pl-9" />
        </label>
        <Select label="Kind" value={kind} onChange={setKind} className="w-auto min-w-36"
          options={[{ value: "all", label: "All kinds" }, ...kinds.map((k) => ({ value: k, label: KIND[k as MemoryKind].label }))]} />
      </div>

      {list.isError ? (
        <ErrorNote message="Couldn't load what Syruss remembers." onRetry={() => list.refetch()} />
      ) : list.isLoading ? (
        <div className="flex flex-col gap-2">{[0, 1, 2].map((i) => <Bar key={i} className="h-12 w-full" />)}</div>
      ) : items.length === 0 && !q && kind === "all" && (list.data?.counts.personal ?? 0) + (list.data?.counts.workspace ?? 0) > 0 ? (
        null
      ) : items.length === 0 ? (
        <Empty title={q || kind !== "all" ? "Nothing matches" : "Nothing yet"}>
          {scope === "personal"
            ? "Tell Syruss how you like things in a chat, or add it here."
            : "Syruss learns as it works and from your answers and reviews. You can also teach it directly."}
        </Empty>
      ) : (
        <ul className={cn("rounded-card border border-border bg-card")}>
          {items.map((m) => (
            <MemoryRow key={m.id} m={m} editable={editable} onEdit={(x) => { setEditing(x); setText(x.content); }} />
          ))}
        </ul>
      )}

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)} title="Edit memory">
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (text.trim().length >= 3) save.mutate(); }}>
          <Textarea aria-label="Memory" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} autoFocus />
          {save.error && <p className="text-body text-negative">{save.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={text.trim().length < 3 || save.isPending}>Save</Button>
          </div>
        </form>
      </Dialog>
      <Dialog open={confirmForget} onOpenChange={setConfirmForget} title="Forget everything about you?"
        description="Syruss forgets every preference and fact it remembers about you in this workspace. Your chats are kept.">
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmForget(false)}>Keep it</Button>
          <Button variant="danger" disabled={forgetMe.isPending} onClick={() => forgetMe.mutate()}>Forget everything</Button>
        </div>
      </Dialog>
    </section>
  );
}

/** What Syruss remembers: privately about you, and shared about the workspace. */
export function MemoryView() {
  const { workspace } = useWorkspace();
  const status = useQuery({ queryKey: ["memories", workspace.id, "personal", "all", ""], queryFn: () => api.memories.list(workspace.id, "personal") });
  return (
    <div className="flex flex-col gap-8">
      {status.data && !status.data.semantic_search && (
        <p className="rounded-card bg-raised px-4 py-3 text-body text-muted">
          Syruss is finding memories by their words. With pgvector in the database it finds them by meaning too.
        </p>
      )}
      <Scope scope="personal" />
      <Scope scope="workspace" />
      <SystemLessons />
    </div>
  );
}
