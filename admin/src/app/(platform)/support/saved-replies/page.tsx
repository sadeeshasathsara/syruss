"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { SupportTabs } from "@/components/support/Bits";
import { Bar, Empty, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { SavedReply } from "@/lib/types";

/** Replies the team reuses. In a ticket, pick one from the menu or type / and its shortcut. */
export default function SavedRepliesPage() {
  const me = useStaff();
  const list = useQuery({ queryKey: ["support", "saved"], queryFn: api.support.savedReplies });
  const [editing, setEditing] = useState<SavedReply | "new" | null>(null);
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Support" subtitle="Saved replies: answers the team sends often. {{first_name}}, {{ticket}} and {{name}} (yours) are filled in." />
      <SupportTabs />
      {me.can("support.respond") && editing === null && <div><Button variant="primary" size="sm" onClick={() => setEditing("new")}>New saved reply</Button></div>}
      {editing !== null && <Editor reply={editing === "new" ? null : editing} onDone={() => setEditing(null)} />}
      {!list.data ? <Bar className="h-40 w-full" /> : !list.data.length ? <Empty title="No saved replies yet">Add the answers the team writes again and again.</Empty> : (
        <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-sheet">
          {list.data.map((r) => (
            <li key={r.id} className="flex flex-col gap-1 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{r.title}</span>
                {r.shortcut && <code className="text-meta text-muted">/{r.shortcut}</code>}
                {me.can("support.respond") && <Button size="sm" onClick={() => setEditing(r)}>Edit</Button>}
              </div>
              <p className="line-clamp-2 whitespace-pre-wrap text-meta text-muted">{r.body}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Editor({ reply, onDone }: { reply: SavedReply | null; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(reply?.title ?? "");
  const [shortcut, setShortcut] = useState(reply?.shortcut ?? "");
  const [body, setBody] = useState(reply?.body ?? "");
  const done = () => { queryClient.invalidateQueries({ queryKey: ["support", "saved"] }); onDone(); };
  const value = { title: title.trim(), body, shortcut: shortcut.trim() || null };
  const save = useMutation({ mutationFn: () => (reply ? api.support.editSavedReply(reply.id, value) : api.support.addSavedReply(value)), onSuccess: done });
  const remove = useMutation({ mutationFn: () => api.support.removeSavedReply(reply!.id), onSuccess: done });
  return (
    <Panel title={reply ? "Edit saved reply" : "New saved reply"}>
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (title.trim() && body.trim()) save.mutate(); }}>
        <div className="flex flex-wrap gap-3">
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-meta text-muted">Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} required className={field} />
          </label>
          <label className="flex w-40 flex-col gap-1 text-meta text-muted">Shortcut (optional)
            <input value={shortcut} onChange={(e) => setShortcut(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} maxLength={40} placeholder="retry-card" className={field} />
          </label>
        </div>
        <label className="flex flex-col gap-1 text-meta text-muted">Reply
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6} maxLength={10000} required
            className={cn(field, "h-auto py-2")} placeholder="Hi {{first_name}}, …" />
        </label>
        <div className="flex gap-2">
          <Button type="submit" size="sm" variant="primary" disabled={!title.trim() || !body.trim() || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
          <Button size="sm" onClick={onDone}>Cancel</Button>
          {reply && <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()} className="ml-auto">Delete</Button>}
        </div>
        <Problem error={save.error ?? remove.error} />
      </form>
    </Panel>
  );
}
