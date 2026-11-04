"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { Dropzone } from "@/components/documents/Dropzone";
import { useShell } from "@/components/shell/ShellContext";
import { Bar, Empty, ErrorNote, PageHeader, Panel, Tabs } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Pill, StatusPill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, docName, fileSize } from "@/lib/format";
import type { AgentQuestion, DocumentItem, DocumentStatus, ReviewTask } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const VIEWS: { key: string; label: string; statuses?: DocumentStatus[] }[] = [
  { key: "", label: "All" },
  { key: "working", label: "Being read", statuses: ["QUEUED", "PROCESSING"] },
  { key: "review", label: "Needs review", statuses: ["NEEDS_REVIEW"] },
  { key: "waiting", label: "Waiting for an answer", statuses: ["AWAITING_INPUT"] },
  { key: "failed", label: "Failed", statuses: ["FAILED"] },
  { key: "done", label: "Done", statuses: ["COMPLETED"] },
];

function Uploader({ onDone }: { onDone: () => void }) {
  const { workspace, href } = useWorkspace();
  const { announce } = useShell();
  const queryClient = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<{ done: number; failed: string[]; total: number } | null>(null);

  const start = async () => {
    const batch = files;
    setProgress({ done: 0, failed: [], total: batch.length });
    const failed: string[] = [];
    let done = 0;
    for (const f of batch) {
      try {
        await api.documents.upload(workspace.id, f);
        done += 1;
      } catch (e) {
        failed.push(`${f.name}: ${(e as Error).message}`);
      }
      setProgress({ done, failed: [...failed], total: batch.length });
    }
    if (done) {
      announce(`You uploaded ${done} document${done === 1 ? "" : "s"}. The agent is reading ${done === 1 ? "it" : "them"} now.`, {
        href: href("documents?view=working"),
        openChat: true,
      });
    }
    queryClient.invalidateQueries({ queryKey: ["documents", workspace.id] });
    setFiles([]);
    if (!failed.length) onDone();
  };

  const busy = progress !== null && progress.done + progress.failed.length < progress.total;
  return (
    <Panel title="Upload documents">
      <div className="flex flex-col gap-3">
        <Dropzone files={files} onChange={setFiles} hint="Each document is read as soon as it's uploaded." />
        {progress && (
          <p className="text-body text-muted" aria-live="polite">
            Uploaded {progress.done} of {progress.total}
          </p>
        )}
        {progress?.failed.map((f) => <p key={f} className="text-body text-negative">{f}</p>)}
        <div className="flex gap-2">
          <Button variant="primary" disabled={!files.length || busy} onClick={start}>
            {busy ? "Uploading…" : `Upload ${files.length || ""}`.trim()}
          </Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </div>
    </Panel>
  );
}

function DocumentRows({ docs }: { docs: DocumentItem[] }) {
  const { href } = useWorkspace();
  return (
    <table className="w-full table-fixed border-collapse text-body">
      <thead className="sr-only @2xl:not-sr-only">
        <tr className="text-left text-meta text-muted">
          <th className="py-2 pr-3 font-normal">Document</th>
          <th className="w-44 py-2 pr-3 font-normal">Status</th>
          <th className="w-24 py-2 pr-3 text-right font-normal">Size</th>
          <th className="w-28 py-2 text-right font-normal">Uploaded</th>
        </tr>
      </thead>
      <tbody>
        {docs.map((d) => (
          <tr key={d.id} className="border-t border-rule hover:bg-raised">
            <td className="py-0" colSpan={1}>
              <Link href={href(`documents/${d.id}`)} className="block py-3 pr-3">
                <span className="block truncate text-ink">{docName(d)}</span>
                {/* Narrow screens: the other columns move to a second line. */}
                <span className="mt-1 flex flex-wrap items-center gap-2 text-meta text-muted @2xl:hidden">
                  <StatusPill status={d.status} />
                  {ago(d.created_at)}
                </span>
                {d.error_message && <span className="mt-1 block truncate text-meta text-negative">{d.error_message}</span>}
              </Link>
            </td>
            <td className="hidden py-3 pr-3 @2xl:table-cell"><StatusPill status={d.status} /></td>
            <td className="num hidden py-3 pr-3 text-right text-muted @2xl:table-cell">{fileSize(d.file_size_bytes)}</td>
            <td className="num hidden py-3 text-right text-muted @2xl:table-cell">{ago(d.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type ReviewRow = {
  id: string;
  title: string;
  detail: string;
  at: string;
  link: string;
  pill: React.ReactNode;
};

/**
 * What waits for a person's review: documents Syruss wants checked and quick decisions it flagged.
 * Conversations (long back-and-forth) are not here; they are under "Waiting for an answer".
 * Details and actions live on the Review page, so every row leads there.
 */
function useReviewRows(docs: DocumentItem[]): { rows: ReviewRow[]; loading: boolean; error: boolean; retry: () => void } {
  const { workspace, href } = useWorkspace();
  const reviews = useQuery({ queryKey: ["reviews", workspace.id], queryFn: () => api.reviews.list(workspace.id) });
  const flags = useQuery({ queryKey: ["questions", workspace.id, "ACTIVE"], queryFn: () => api.questions.list(workspace.id, "ACTIVE") });
  const nameOf = (id: string | null | undefined) => {
    const d = id ? docs.find((x) => x.id === id) : undefined;
    return d ? docName(d) : "Document";
  };
  const rows: ReviewRow[] = [
    ...(reviews.data ?? []).map((t: ReviewTask) => ({
      id: t.id, title: nameOf(t.document_id), detail: t.reason, at: t.created_at,
      link: href(`review?item=${t.id}`), pill: <Pill tone="warning">Check</Pill>,
    })),
    ...(flags.data ?? []).map((q: AgentQuestion) => ({
      id: q.id, title: q.related_document_id ? nameOf(q.related_document_id) : q.question,
      detail: q.related_document_id ? q.question : q.context, at: q.created_at, link: href(`review?flag=${q.id}`),
      pill: q.status === "ANSWERED" ? <Pill tone="neutral">Pending</Pill> : <Pill tone="info">Decision</Pill>,
    })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  return {
    rows,
    loading: reviews.isLoading || flags.isLoading,
    error: reviews.isError || flags.isError,
    retry: () => { reviews.refetch(); flags.refetch(); },
  };
}

function ReviewRows({ rows }: { rows: ReviewRow[] }) {
  return (
    <ul className="flex flex-col">
      {rows.map((r) => (
        <li key={r.id} className="border-t border-rule hover:bg-raised">
          <Link href={r.link} className="flex items-center gap-3 py-3 pr-3">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-ink">{r.title}</span>
              <span className="block truncate text-meta text-muted">{r.detail}</span>
            </span>
            {r.pill}
            <span className="num hidden w-28 flex-none text-right text-meta text-muted @2xl:block">{ago(r.at)}</span>
            <ArrowRight className="size-4 flex-none text-faint" aria-hidden />
          </Link>
        </li>
      ))}
    </ul>
  );
}

function DocumentsScreen() {
  const { workspace, href } = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const view = params.get("view") ?? "";
  const uploading = params.get("upload") === "1";
  const [query, setQuery] = useState("");
  const documents = useQuery({
    queryKey: ["documents", workspace.id],
    queryFn: () => api.documents.list(workspace.id, undefined, 500),
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === "QUEUED" || d.status === "PROCESSING") ? 5000 : false),
  });

  const all = useMemo(() => documents.data ?? [], [documents.data]);
  const current = VIEWS.find((v) => v.key === view) ?? VIEWS[0];
  const review = useReviewRows(all);
  const reviewing = current.key === "review";
  const rows = useMemo(
    () =>
      all.filter(
        (d) =>
          (!current.statuses || current.statuses.includes(d.status)) &&
          (!query.trim() || (docName(d) + " " + d.original_filename).toLowerCase().includes(query.trim().toLowerCase())),
      ),
    [all, current, query],
  );
  const needle = query.trim().toLowerCase();
  const reviewRows = review.rows.filter((r) => !needle || (r.title + " " + r.detail).toLowerCase().includes(needle));
  const shown = reviewing ? reviewRows.length : rows.length;
  const setUpload = (open: boolean) => router.replace(href(`documents${open ? "?upload=1" : view ? `?view=${view}` : ""}`));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Documents"
        subtitle={documents.data ? `${all.length} document${all.length === 1 ? "" : "s"} in ${workspace.name}.` : " "}
        actions={!uploading && <Button variant="primary" onClick={() => setUpload(true)}>Upload documents</Button>}
      />

      {uploading && <Uploader onDone={() => setUpload(false)} />}

      <Tabs
        items={VIEWS.map((v) => ({
          href: href(`documents${v.key ? `?view=${v.key}` : ""}`),
          label: v.label,
          count: v.key === "review"
            ? (review.loading ? undefined : review.rows.length)
            : documents.data ? all.filter((d) => !v.statuses || v.statuses.includes(d.status)).length : undefined,
          active: v.key === current.key,
        }))}
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <span className="sr-only">Search documents</span>
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by file name" className="pl-9" />
        </label>
        <span className="num text-body text-muted">{shown} shown</span>
      </div>

      {reviewing && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-border bg-card p-3">
          <p className="min-w-0 flex-1 text-body text-muted">
            Open an item to see the page, what Syruss noted, and approve or correct it on the Review page.
          </p>
          <Button asChild variant="secondary">
            <Link href={href("review")}>Go to Review <ArrowRight className="size-4" aria-hidden /></Link>
          </Button>
        </div>
      )}

      {documents.isError && <ErrorNote message="Couldn't load documents." onRetry={() => documents.refetch()} />}
      {reviewing && review.error && <ErrorNote message="Couldn't load what's waiting for review." onRetry={review.retry} />}
      {reviewing ? (
        documents.isLoading || review.loading ? (
          <div className="flex flex-col gap-4">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : reviewRows.length === 0 ? (
          <p className="text-body text-muted">Nothing is waiting for review{query ? ` matching “${query}”` : ""}.</p>
        ) : (
          <ReviewRows rows={reviewRows} />
        )
      ) : documents.isLoading ? (
        <div className="flex flex-col gap-4">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
      ) : rows.length === 0 ? (
        all.length === 0 ? (
          <Empty title="No documents yet" action={<Button variant="secondary" onClick={() => setUpload(true)}>Upload documents</Button>}>
            Upload a batch. The agent reads each one, records what it finds, and asks you only when it can’t be sure.
          </Empty>
        ) : (
          <p className="text-body text-muted">No documents in this view{query ? ` matching “${query}”` : ""}.</p>
        )
      ) : (
        <DocumentRows docs={rows} />
      )}
    </div>
  );
}

export default function DocumentsPage() {
  return (
    <Suspense>
      <DocumentsScreen />
    </Suspense>
  );
}
