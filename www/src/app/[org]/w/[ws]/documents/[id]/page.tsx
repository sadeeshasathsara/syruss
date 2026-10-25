"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAgentChat } from "@/components/chat/ChatProvider";
import { PageViewer } from "@/components/documents/PageViewer";
import { RecordView } from "@/components/documents/RecordView";
import { useShell } from "@/components/shell/ShellContext";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { StatusPill } from "@/components/ui/Pill";
import { ApiError, api } from "@/lib/api";
import { ago, percent } from "@/lib/format";
import type { Extraction } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

export default function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const { workspace, href } = useWorkspace();
  const { announce } = useShell();
  const queryClient = useQueryClient();
  const wid = workspace.id;

  const doc = useQuery({
    queryKey: ["documents", wid, id],
    queryFn: () => api.documents.get(wid, id),
    refetchInterval: (q) => (q.state.data && ["QUEUED", "PROCESSING"].includes(q.state.data.status) ? 4000 : false),
  });
  const extraction = useQuery({
    queryKey: ["extraction", wid, id],
    queryFn: () => api.documents.extraction(wid, id),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  });
  const template = useQuery({ queryKey: ["template", wid], queryFn: () => api.template.get(wid) });
  const reviews = useQuery({ queryKey: ["reviews", wid], queryFn: () => api.reviews.list(wid) });
  const review = reviews.data?.find((r) => r.document_id === id);
  const access = useQuery({ queryKey: ["settings", wid], queryFn: () => api.settings.get(wid) });
  const canCorrect = !!access.data?.access.permissions.includes("documents.review");
  const { openConversation } = useAgentChat();
  // A saved record is corrected box by box; the change and the reason go to Syruss in the chat.
  const correct = async (object: string, next: Extraction["extracted_data"], reason: string) => {
    const res = await api.documents.correct(wid, id, next, [{ object, reason }]);
    await queryClient.invalidateQueries({ queryKey: ["extraction", wid, id] });
    openConversation(res.conversation_id);
  };

  const reread = useMutation({
    mutationFn: () => api.documents.reprocess(wid, id),
    onSuccess: () => {
      announce(`You asked the agent to read ${doc.data?.original_filename ?? "a document"} again`, { href: href(`documents/${id}`), openChat: true });
      queryClient.invalidateQueries({ queryKey: ["documents", wid] });
    },
  });

  if (doc.error instanceof ApiError && doc.error.status === 404) {
    return (
      <div className="flex flex-col gap-3">
        <PageHeader title="Document not found" back={{ href: href("documents"), label: "Documents" }} />
        <p className="text-body text-muted">It may have been deleted, or it belongs to another workspace.</p>
      </div>
    );
  }
  if (!doc.data) {
    return doc.isError ? <ErrorNote message="Couldn't load this document." onRetry={() => doc.refetch()} /> : <Bar className="h-6 w-72" />;
  }

  const d = doc.data;
  const e = extraction.data;
  const working = d.status === "QUEUED" || d.status === "PROCESSING";
  const errors = e?.validation?.errors ?? [];
  const signals = e?.validation?.signals ?? [];
  const subtitle = working
    ? "The agent is reading this document. Follow it in the chat."
    : d.status === "FAILED"
      ? d.error_message ?? "The agent couldn't read this document."
      : d.status === "AWAITING_INPUT"
        ? "Waiting for your answer to the agent's question."
        : e
          ? `Read ${ago(e.updated_at).toLowerCase()}, ${percent(e.overall_confidence)} sure overall.`
          : `Uploaded ${ago(d.created_at).toLowerCase()}.`;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        back={{ href: href("documents"), label: "Documents" }}
        title={d.original_filename}
        subtitle={<span className="flex flex-wrap items-center gap-2"><StatusPill status={d.status} />{subtitle}</span>}
        actions={
          <>
            {review && (
              <Button asChild variant="primary">
                <Link href={href(`review?item=${review.id}`)}>Review</Link>
              </Button>
            )}
            {!working && (
              <Button variant="secondary" disabled={reread.isPending} onClick={() => reread.mutate()}>
                Read again
              </Button>
            )}
          </>
        }
      />
      {reread.error && <p className="text-body text-negative">{reread.error.message}</p>}

      {/* The page beside its record. Side by side when the sheet is wide enough, stacked otherwise. */}
      <div className="grid grid-cols-1 items-start gap-5 @5xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <PageViewer
          src={api.documents.fileUrl(wid, id)}
          alt={`Scanned page: ${d.original_filename}`}
          className="h-[60dvh] @5xl:sticky @5xl:top-0 @5xl:h-[calc(100dvh-140px)]"
        />
        <div className="flex min-w-0 flex-col gap-5">
          {review && (
            <Panel title="Why it needs a person">
              <p className="text-body text-ink">{review.reason}</p>
            </Panel>
          )}
          {e ? (
            <>
              <RecordView
                data={e.extracted_data}
                schema={template.data?.schema_definition}
                evidence={e.evidence_records}
                uncertain={review?.uncertain_fields}
                onCorrect={d.status === "COMPLETED" && canCorrect ? correct : undefined}
              />
              {errors.length > 0 && (
                <Panel title="Checks that failed">
                  <ul className="flex flex-col gap-1">
                    {errors.map((err, i) => <li key={i} className="text-body text-ink">{err.message}</li>)}
                  </ul>
                </Panel>
              )}
              {signals.length > 0 && (
                <Panel title="Identity checks">
                  <ul className="flex flex-col gap-3">
                    {signals.map((s) => (
                      <li key={s.id} className="flex flex-col gap-0.5">
                        <span className="text-body text-ink">{s.message.split(" Look at the page again")[0]}</span>
                        {s.acknowledged && <span className="text-meta text-muted">The agent looked again: {s.reason}</span>}
                      </li>
                    ))}
                  </ul>
                </Panel>
              )}
              {e.agent_notes && (
                <section>
                  <h3 className="text-section font-semibold text-ink">Agent’s notes</h3>
                  <p className="mt-1 whitespace-pre-wrap text-body text-muted">{e.agent_notes}</p>
                </section>
              )}
            </>
          ) : extraction.isLoading ? (
            <div className="flex flex-col gap-3">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
          ) : (
            <p className="text-body text-muted">{working ? "The record appears here when the agent finishes." : "No record yet."}</p>
          )}
        </div>
      </div>
    </div>
  );
}
