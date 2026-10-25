"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ProposalCard } from "@/components/chat/cards";
import { useAgentChat } from "@/components/chat/ChatProvider";
import { PageViewer } from "@/components/documents/PageViewer";
import { Bar, Empty, ErrorNote, PageHeader, PickOne } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { Markdown } from "@/components/ui/Markdown";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import type { ConversationSummary, Proposal } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const PROPOSAL_KIND: Record<Proposal["proposal_type"], string> = {
  TEMPLATE_CHANGE: "Template change",
  ENTITY_CREATE: "New person or thing",
  ENTITY_MERGE: "Merge duplicates",
  SOURCE_MAPPING: "Data source mapping",
};

function useDocumentNames() {
  const { workspace } = useWorkspace();
  const docs = useQuery({ queryKey: ["documents", workspace.id], queryFn: () => api.documents.list(workspace.id, undefined, 500) });
  return (id: string) => docs.data?.find((d) => d.id === id)?.original_filename ?? "Document";
}

/** The documents an item is about: the first one to look at, the rest as links. */
function Documents({ ids }: { ids: string[] }) {
  const { workspace, href } = useWorkspace();
  const nameOf = useDocumentNames();
  if (!ids.length) return null;
  return (
    <div className="flex flex-col gap-2">
      <PageViewer src={api.documents.fileUrl(workspace.id, ids[0])} alt="Scanned page" className="h-[50dvh] @5xl:h-[calc(100dvh-220px)]" />
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {ids.map((id) => (
          <li key={id}><Link href={href(`documents/${id}`)} className="text-body text-muted hover:text-ink hover:underline">{nameOf(id)}</Link></li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A conversation Syruss started: what has been said, and the documents it is about. Replying here
 * sends your message into the conversation and opens it in the chat panel, where it continues.
 */
function ConversationDetail({ id }: { id: string }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const { openConversation } = useAgentChat();
  const conv = useQuery({ queryKey: ["conversation", workspace.id, id], queryFn: () => api.chat.get(workspace.id, id) });
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!conv.data) return conv.isError ? <ErrorNote message="Couldn't load this conversation." /> : <Bar className="h-40 w-full" />;
  const c = conv.data;
  const docIds = ((c.context_metadata?.document_ids as string[] | undefined) ?? []).filter(Boolean);
  const resolved = c.status === "resolved";

  const reply = async () => {
    if (!text.trim()) return;
    setSending(true);
    setError(null);
    try {
      await api.chat.send(workspace.id, id, text.trim(), { current_page: "agent" });
      setText("");
      openConversation(id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", workspace.id, id] }),
        queryClient.invalidateQueries({ queryKey: ["conversations", workspace.id] }),
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't send your reply.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className={cn("grid grid-cols-1 items-start gap-5", docIds.length && "@5xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]")}>
      {docIds.length > 0 && <Documents ids={docIds} />}
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="min-w-0 flex-1 text-section font-semibold text-ink">{c.title}</h2>
          <Pill tone={resolved ? "positive" : "info"}>{resolved ? "Resolved" : "Pending"}</Pill>
        </div>
        <ol className="flex flex-col gap-3">
          {(c.messages ?? []).map((m) => (
            <li key={m.id} className={cn("flex", m.role === "user" && "justify-end")}>
              {m.role === "user" ? (
                <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-card rounded-br-chip bg-bubble px-3 py-2 text-body text-ink">{m.content}</div>
              ) : m.role === "assistant" ? (
                <div className="min-w-0 max-w-full">
                  <p className="mb-0.5 text-meta text-muted">Syruss, {ago(m.created_at).toLowerCase()}</p>
                  <Markdown>{m.content}</Markdown>
                </div>
              ) : (
                <p className="text-meta text-muted">{m.content}</p>
              )}
            </li>
          ))}
        </ol>
        {resolved && c.resolution && <p className="rounded-control bg-raised px-3 py-2 text-body text-ink">Outcome: {c.resolution}</p>}
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            reply();
          }}
        >
          <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={resolved ? "Write to reopen it" : "Reply to Syruss"} className="min-h-20" aria-label="Your reply" />
          {error && <p className="text-body text-negative">{error}</p>}
          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" disabled={!text.trim() || sending}>{sending ? "Sending…" : "Reply in chat"}</Button>
            <span className="text-meta text-muted">The conversation continues in the chat panel.</span>
          </div>
        </form>
      </div>
    </div>
  );
}

function ProposalDetail({ proposal }: { proposal: Proposal }) {
  return (
    <div className={cn("grid grid-cols-1 items-start gap-5", proposal.evidence_document_ids.length && "@5xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]")}>
      {proposal.evidence_document_ids.length > 0 && <Documents ids={proposal.evidence_document_ids} />}
      <ProposalCard proposal={proposal} />
    </div>
  );
}

type InboxItem =
  | { kind: "conversation"; id: string; title: string; status: string; at: string; conversation: ConversationSummary }
  | { kind: "proposal"; id: string; title: string; status: string; at: string; proposal: Proposal };

function InboxList({ items, selectedId, onOpen }: { items: InboxItem[]; selectedId: string | null; onOpen: (item: InboxItem) => void }) {
  return (
    <ul className="flex flex-col @4xl:max-h-[calc(100dvh-200px)] @4xl:overflow-y-auto @4xl:scrollbar-thin">
      {items.map((item) => {
        const done = ["resolved", "APPROVED", "REJECTED"].includes(item.status);
        return (
          <li key={item.id}>
            <button
              type="button"
              onClick={() => onOpen(item)}
              aria-current={item.id === selectedId ? "true" : undefined}
              className={cn("flex w-full cursor-pointer flex-col gap-0.5 rounded-control px-3 py-2.5 text-left",
                item.id === selectedId ? "bg-active" : "hover:bg-raised")}
            >
              <span className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{item.title}</span>
                <span className="num flex-none text-meta text-muted">{ago(item.at)}</span>
              </span>
              <span className="flex items-center gap-2">
                <Pill tone={done ? "positive" : "info"}>
                  {item.kind === "conversation" ? (done ? "Resolved" : "Pending") : item.status === "PENDING" ? "Needs approval" : item.status === "APPROVED" ? "Approved" : "Rejected"}
                </Pill>
                <span className="truncate text-meta text-muted">
                  {item.kind === "conversation" ? "Conversation" : PROPOSAL_KIND[item.proposal.proposal_type]}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Syruss's conversations and proposed changes: pending ones (or, in history, decided ones). */
function Inbox({ history }: { history: boolean }) {
  const { workspace, href } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const conversations = useInfiniteQuery({
    queryKey: ["conversations", workspace.id, "agent", history ? "resolved" : "open"],
    queryFn: ({ pageParam }) => api.chat.list(workspace.id, { kind: "agent", status: history ? "resolved" : "open", offset: pageParam, limit: 20 }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.next_offset : undefined),
  });
  const proposals = useQuery({
    queryKey: ["proposals", workspace.id, history ? "DECIDED" : "PENDING"],
    queryFn: async () => (history
      ? [...await api.proposals.list(workspace.id, "APPROVED"), ...await api.proposals.list(workspace.id, "REJECTED")]
      : api.proposals.list(workspace.id)),
  });
  const items: InboxItem[] = [
    ...(conversations.data?.pages.flatMap((p) => p.items) ?? []).map((c) => ({
      kind: "conversation" as const, id: c.id, title: c.title, status: c.status ?? "open", at: c.updated_at, conversation: c,
    })),
    ...(proposals.data ?? []).map((p) => ({
      kind: "proposal" as const, id: p.id, title: p.proposed_data.change_summary || p.observation, status: p.status,
      at: p.reviewed_at ?? p.created_at, proposal: p,
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const base = history ? "agent?tab=history" : "agent";
  const selectedId = params.get("conversation") ?? params.get("proposal");
  const selected = items.find((i) => i.id === selectedId);
  const open = (item: InboxItem | null) =>
    router.replace(href(item ? `${base}${history ? "&" : "?"}${item.kind}=${item.id}` : base), { scroll: false });
  const loading = conversations.isLoading || proposals.isLoading;

  return (
    <div className="flex flex-col gap-5">
      <div className={cn(selected && "hidden @4xl:block")}>
        <PageHeader
          title={history ? "Decided" : "Agent inbox"}
          subtitle={loading || !items.length ? undefined : history
            ? "Conversations that were settled and changes your team decided, newest first."
            : `${items.length} thing${items.length === 1 ? "" : "s"} Syruss needs your team for: conversations and changes to approve.`}
        />
      </div>
      {(conversations.isError || proposals.isError) && <ErrorNote message="Couldn't load the agent inbox." />}
      {loading && <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-12 w-full" />)}</div>}
      {!loading && items.length === 0 && (history ? (
        <Empty title="Nothing decided yet">Settled conversations and the changes your team approves or rejects are kept here.</Empty>
      ) : (
        <Empty title="Nothing waiting">
          When something needs a discussion, or Syruss proposes a change to the template or the data, it appears here.
          Quick decisions wait in Review.
        </Empty>
      ))}
      {items.length > 0 && (
        <div className="grid grid-cols-1 gap-6 @4xl:grid-cols-[300px_minmax(0,1fr)]">
          <div className={cn("@4xl:border-r @4xl:border-border @4xl:pr-4", selected && "hidden @4xl:block")}>
            <InboxList items={items} selectedId={selectedId} onOpen={open} />
            {conversations.hasNextPage && (
              <Button variant="ghost" className="mt-2" disabled={conversations.isFetchingNextPage} onClick={() => conversations.fetchNextPage()}>
                {conversations.isFetchingNextPage ? "Loading…" : "Show more"}
              </Button>
            )}
          </div>
          {selected ? (
            <div className="min-w-0">
              <button type="button" onClick={() => open(null)} className="mb-3 inline-flex items-center gap-1 text-body text-muted hover:text-ink @4xl:hidden">
                <ArrowLeft className="size-4" /> Back
              </button>
              {selected.kind === "conversation"
                ? <ConversationDetail key={selected.id} id={selected.id} />
                : <ProposalDetail key={selected.id} proposal={selected.proposal} />}
              {selected.kind === "proposal" && selected.proposal.reviewed_at && (
                <p className="mt-3 text-meta text-muted">Decided {dateTime(selected.proposal.reviewed_at)}</p>
              )}
            </div>
          ) : (
            <PickOne title="Choose one from the list">The conversation or the proposed change, with its documents, appears here.</PickOne>
          )}
        </div>
      )}
    </div>
  );
}

function AgentInbox() {
  return <Inbox history={useSearchParams().get("tab") === "history"} />;
}

export default function AgentInboxPage() {
  return (
    <Suspense>
      <AgentInbox />
    </Suspense>
  );
}
