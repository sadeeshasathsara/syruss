"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { QuestionCard } from "@/components/chat/cards";
import { PageViewer } from "@/components/documents/PageViewer";
import { RecordView } from "@/components/documents/RecordView";
import { useShell } from "@/components/shell/ShellContext";
import { Bar, Empty, ErrorNote, PageHeader, PickOne } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { Markdown } from "@/components/ui/Markdown";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import { Pill } from "@/components/ui/Pill";
import type { AgentQuestion, ReviewTask } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

function ReviewItem({ task, onDone }: { task: ReviewTask; onDone: () => void }) {
  const { workspace, href } = useWorkspace();
  const { announce } = useShell();
  const queryClient = useQueryClient();
  const wid = workspace.id;
  const template = useQuery({ queryKey: ["template", wid], queryFn: () => api.template.get(wid) });
  const doc = useQuery({ queryKey: ["documents", wid, task.document_id], queryFn: () => api.documents.get(wid, task.document_id) });
  const extraction = useQuery({ queryKey: ["extraction", wid, task.document_id], queryFn: () => api.documents.extraction(wid, task.document_id) });
  const [data, setData] = useState(task.original_data);
  const [notes, setNotes] = useState("");

  const changed = JSON.stringify(data) !== JSON.stringify(task.original_data);
  const decide = useMutation({
    mutationFn: (action: "APPROVE" | "CORRECT" | "REJECT") =>
      api.reviews.decide(wid, task.id, action, action === "CORRECT" ? data : undefined, notes.trim() || undefined),
    onSuccess: (_, action) => {
      const name = doc.data?.original_filename ?? "a document";
      announce(
        action === "REJECT" ? `You rejected ${name}` : action === "CORRECT" ? `You corrected ${name}. The agent is learning from it.` : `You approved ${name}`,
        { href: href(`documents/${task.document_id}`) },
      );
      ["reviews", "documents", "workspace"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
      onDone();
    },
  });

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-section font-semibold text-ink">
          <Link href={href(`documents/${task.document_id}`)} className="hover:underline">{doc.data?.original_filename ?? "Document"}</Link>
        </h2>
        <p className="mt-1 text-body text-ink">{task.reason}</p>
      </div>
      <WhatHappened documentId={task.document_id} />
      <div className="grid grid-cols-1 items-start gap-5 @5xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <PageViewer src={api.documents.fileUrl(wid, task.document_id)} alt="Scanned page" className="h-[50dvh] @5xl:h-[calc(100dvh-180px)]" />
        <div className="flex min-w-0 flex-col gap-5">
          <RecordView
            data={data}
            schema={template.data?.schema_definition}
            evidence={extraction.data?.evidence_records}
            uncertain={task.uncertain_fields}
            editable
            onChange={setData}
          />
          <label className="flex flex-col gap-2">
            <span className="text-body font-medium text-ink">Note for the agent (optional)</span>
            <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What did it get wrong, and how can it tell next time?" />
          </label>
          {decide.error && <p className="text-body text-negative">{decide.error.message}</p>}
          <div className="flex flex-wrap gap-2">
            {changed ? (
              <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate("CORRECT")}>Save corrections</Button>
            ) : (
              <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate("APPROVE")}>Approve as read</Button>
            )}
            {changed && <Button variant="secondary" onClick={() => setData(task.original_data)}>Undo changes</Button>}
            <Button variant="danger" disabled={decide.isPending} onClick={() => decide.mutate("REJECT")}>Reject document</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** What Syruss noted about a record: its own notes, and the checks that flagged something. */
function WhatHappened({ documentId }: { documentId: string }) {
  const { workspace } = useWorkspace();
  const extraction = useQuery({ queryKey: ["extraction", workspace.id, documentId], queryFn: () => api.documents.extraction(workspace.id, documentId) });
  const v = extraction.data?.validation;
  const notes = [
    ...(v?.errors ?? []).map((e) => e.message),
    ...(v?.signals ?? []).map((s) => s.message.split(" Look at the page again")[0]),
  ];
  if (!extraction.data?.agent_notes && notes.length === 0) return null;
  return (
    <section className="rounded-card border border-border bg-card p-3">
      <h3 className="text-body font-semibold text-ink">What Syruss noted</h3>
      {extraction.data?.agent_notes && <Markdown className="mt-1">{extraction.data.agent_notes}</Markdown>}
      {notes.length > 0 && (
        <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-body text-ink">
          {notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      )}
    </section>
  );
}

/** What was read from a document so far, as the template lays it out (tables for repeated rows). Nothing when nothing was saved yet. */
function WhatWasRead({ documentId }: { documentId: string }) {
  const { workspace } = useWorkspace();
  const template = useQuery({ queryKey: ["template", workspace.id], queryFn: () => api.template.get(workspace.id) });
  const extraction = useQuery({
    queryKey: ["extraction", workspace.id, documentId],
    queryFn: () => api.documents.extraction(workspace.id, documentId),
    retry: false,
  });
  const data = extraction.data?.extracted_data;
  if (!data || Object.keys(data).length === 0) return null;
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h3 className="text-body font-semibold text-ink">What was read</h3>
      <RecordView data={data} schema={template.data?.schema_definition} evidence={extraction.data?.evidence_records} />
    </section>
  );
}

/** A quick decision Syruss flagged: what it saw, the document, and the answer. */
function FlagItem({ question }: { question: AgentQuestion }) {
  const { workspace, href } = useWorkspace();
  const docId = question.related_document_id ?? question.evidence_document_ids[0];
  const others = question.evidence_document_ids.filter((d) => d !== docId);
  const docs = useQuery({ queryKey: ["documents", workspace.id], queryFn: () => api.documents.list(workspace.id, undefined, 500) });
  const nameOf = (id: string) => docs.data?.find((d) => d.id === id)?.original_filename ?? "Document";
  return (
    <div className="flex flex-col gap-5">
      <div className={cn("grid grid-cols-1 items-start gap-5", docId && "@5xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]")}>
        {docId && (
          <div className="flex flex-col gap-2">
            <PageViewer src={api.documents.fileUrl(workspace.id, docId)} alt="Scanned page" className="h-[50dvh] @5xl:h-[calc(100dvh-220px)]" />
            <Link href={href(`documents/${docId}`)} className="text-body text-muted hover:text-ink hover:underline">{nameOf(docId)}</Link>
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-4">
          <QuestionCard question={question} />
          {docId && !question.data_table && <WhatWasRead documentId={docId} />}
          {docId && <WhatHappened documentId={docId} />}
          {others.length > 0 && (
            <section>
              <h3 className="text-body font-semibold text-ink">Also seen on</h3>
              <ul className="mt-1 flex flex-col gap-1">
                {others.map((d) => (
                  <li key={d}><Link href={href(`documents/${d}`)} className="text-body text-ink hover:underline">{nameOf(d)}</Link></li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

type QueueItem =
  | { kind: "review"; id: string; title: string; detail: string; at: string; task: ReviewTask }
  | { kind: "flag"; id: string; title: string; detail: string; at: string; question: AgentQuestion };

function ReviewQueue() {
  const { workspace, href } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const reviews = useQuery({ queryKey: ["reviews", workspace.id], queryFn: () => api.reviews.list(workspace.id) });
  // Open flags, and answered ones Syruss is still working on (pending until it resolves their thread)
  const flags = useQuery({ queryKey: ["questions", workspace.id, "ACTIVE"], queryFn: () => api.questions.list(workspace.id, "ACTIVE") });
  const docs = useQuery({ queryKey: ["documents", workspace.id], queryFn: () => api.documents.list(workspace.id, undefined, 500) });
  const nameOf = (docId: string) => docs.data?.find((d) => d.id === docId)?.original_filename ?? "Document";
  const items: QueueItem[] = [
    ...(reviews.data ?? []).map((t) => ({ kind: "review" as const, id: t.id, title: nameOf(t.document_id), detail: t.reason, at: t.created_at, task: t })),
    ...(flags.data ?? []).map((q) => ({ kind: "flag" as const, id: q.id, title: q.question, detail: q.context, at: q.created_at, question: q })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  const selectedId = params.get("item") ?? params.get("flag");
  const selected = items.find((i) => i.id === selectedId);
  const open = (item: QueueItem | null) =>
    router.replace(href(`review${item ? `?${item.kind === "flag" ? "flag" : "item"}=${item.id}` : ""}`), { scroll: false });
  const loading = reviews.isLoading || flags.isLoading;
  const counts = { reviews: reviews.data?.length ?? 0, flags: (flags.data ?? []).filter((q) => q.status === "OPEN").length };

  const next = () => {
    const i = items.findIndex((r) => r.id === selectedId);
    const following = items[i + 1] ?? items[i - 1];
    open(following && following.id !== selectedId ? following : null);
  };

  const summary = [
    counts.reviews ? `${counts.reviews} document${counts.reviews === 1 ? "" : "s"} to check` : "",
    counts.flags ? `${counts.flags} decision${counts.flags === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(" and ");

  return (
    <div className="flex flex-col gap-5">
      <div className={cn(selected && "hidden @4xl:block")}>
        <PageHeader title="Review" subtitle={loading ? " " : items.length ? `${summary} waiting for you, oldest first.` : undefined} />
      </div>
      {(reviews.isError || flags.isError) && <ErrorNote message="Couldn't load what's waiting for review." onRetry={() => { reviews.refetch(); flags.refetch(); }} />}
      {loading && <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-12 w-full" />)}</div>}
      {!loading && items.length === 0 && (
        <Empty title="All caught up" action={<Button asChild variant="secondary"><Link href={href("documents")}>View documents</Link></Button>}>
          When Syruss isn&apos;t sure about a document, or needs a quick decision from you, it waits here.
        </Empty>
      )}
      {items.length > 0 && (
        <div className="grid grid-cols-1 gap-6 @4xl:grid-cols-[300px_minmax(0,1fr)]">
          <ul className={cn("flex flex-col @4xl:max-h-[calc(100dvh-200px)] @4xl:overflow-y-auto @4xl:border-r @4xl:border-border @4xl:pr-4 @4xl:scrollbar-thin", selected && "hidden @4xl:flex")}>
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => open(item)}
                  aria-current={item.id === selectedId ? "true" : undefined}
                  className={cn(
                    "flex w-full cursor-pointer flex-col gap-0.5 rounded-control px-3 py-2.5 text-left",
                    item.id === selectedId ? "bg-active" : "hover:bg-raised",
                  )}
                >
                  <span className="flex items-baseline gap-2">
                    <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{item.title}</span>
                    <span className="num flex-none text-meta text-muted">{ago(item.at)}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    {item.kind === "flag" && item.question.status === "ANSWERED"
                      ? <Pill tone="neutral">Pending</Pill>
                      : <Pill tone={item.kind === "flag" ? "info" : "warning"}>{item.kind === "flag" ? "Decision" : "Check"}</Pill>}
                    <span className="min-w-0 flex-1 truncate text-body text-muted">{item.detail}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {selected ? (
            <div className="min-w-0">
              <button type="button" onClick={() => open(null)} className="mb-3 inline-flex items-center gap-1 text-body text-muted hover:text-ink @4xl:hidden">
                <ArrowLeft className="size-4" /> Everything to review
              </button>
              {selected.kind === "review"
                ? <ReviewItem key={selected.id} task={selected.task} onDone={next} />
                : <FlagItem key={selected.id} question={selected.question} />}
            </div>
          ) : (
            <PickOne title="Choose one from the list">The document, what Syruss noted and what to decide appear here.</PickOne>
          )}
        </div>
      )}
    </div>
  );
}

const OUTCOME: Record<string, { label: string; tone: "positive" | "info" | "negative" }> = {
  APPROVED: { label: "Approved", tone: "positive" },
  CORRECTED: { label: "Corrected", tone: "info" },
  REJECTED: { label: "Rejected", tone: "negative" },
  ANSWERED: { label: "Answered", tone: "positive" },
  DISMISSED: { label: "Dismissed", tone: "info" },
};

/** What people already decided (reviews and quick decisions), newest first. */
function ReviewHistory() {
  const { workspace, href } = useWorkspace();
  const reviews = useQuery({ queryKey: ["reviews", workspace.id, "decided"], queryFn: () => api.reviews.list(workspace.id, true) });
  const answered = useQuery({ queryKey: ["questions", workspace.id, "ALL"], queryFn: () => api.questions.list(workspace.id, "ALL") });
  const docs = useQuery({ queryKey: ["documents", workspace.id], queryFn: () => api.documents.list(workspace.id, undefined, 500) });
  const nameOf = (docId: string) => docs.data?.find((d) => d.id === docId)?.original_filename ?? "Document";
  const rows = [
    ...(reviews.data ?? []).map((r) => ({
      id: r.id, title: nameOf(r.document_id), detail: r.reviewer_notes || r.reason, status: r.status,
      who: r.decided_by, at: r.decided_at ?? r.created_at, link: href(`documents/${r.document_id}`),
    })),
    ...(answered.data ?? []).filter((q) => q.status !== "OPEN").map((q) => ({
      id: q.id, title: q.question, detail: q.answer ?? "", status: q.status, who: q.answered_by,
      at: q.answered_at ?? q.created_at, link: q.related_document_id ? href(`documents/${q.related_document_id}`) : undefined,
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const loading = reviews.isLoading || answered.isLoading;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Reviewed" subtitle={loading ? " " : rows.length ? `${rows.length} decision${rows.length === 1 ? "" : "s"} by your team, newest first.` : undefined} />
      {(reviews.isError || answered.isError) && <ErrorNote message="Couldn't load the review history." onRetry={() => { reviews.refetch(); answered.refetch(); }} />}
      {loading ? (
        <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-12 w-full" />)}</div>
      ) : rows.length === 0 ? (
        <Empty title="Nothing reviewed yet">Your team&apos;s review decisions and answers to Syruss are listed here.</Empty>
      ) : (
        <ul className="flex flex-col">
          {rows.map((r) => {
            const body = (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body font-medium text-ink">{r.title}</span>
                  <span className="block truncate text-meta text-muted">{r.detail}</span>
                </span>
                {OUTCOME[r.status] && <Pill tone={OUTCOME[r.status].tone}>{OUTCOME[r.status].label}</Pill>}
                <span className="w-44 flex-none text-right text-meta text-muted">{r.who ? `${r.who}, ` : ""}{dateTime(r.at)}</span>
              </>
            );
            return (
              <li key={r.id} className="border-b border-rule last:border-0">
                {r.link ? (
                  <Link href={r.link} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3 hover:bg-raised/60">{body}</Link>
                ) : (
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3">{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ReviewTabs() {
  return useSearchParams().get("tab") === "history" ? <ReviewHistory /> : <ReviewQueue />;
}

export default function ReviewPage() {
  return (
    <Suspense>
      <ReviewTabs />
    </Suspense>
  );
}
