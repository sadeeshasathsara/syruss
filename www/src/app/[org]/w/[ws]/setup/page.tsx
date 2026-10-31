"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAgentChat } from "@/components/chat/ChatProvider";
import { Dropzone } from "@/components/documents/Dropzone";
import { useShell } from "@/components/shell/ShellContext";
import { Bar, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { ImageViewer, type ViewerImage } from "@/components/ui/ImageViewer";
import { Dialog } from "@/components/ui/Sheet";
import { normalizeSchema, SchemaEditor } from "@/components/workspace/SchemaEditor";
import { api } from "@/lib/api";
import type { TemplateSchema } from "@/lib/types";
import { useLive } from "@/lib/live";
import { useWorkspace } from "@/lib/workspace";

/**
 * Designing the template, inside the draft workspace: upload samples, the agent studies them
 * (live in the chat), proposes what to capture and asks about anything unclear; the admin edits
 * and publishes. Published workspaces come here to refine with new samples.
 */
export default function TemplateSetup() {
  const { workspace, href } = useWorkspace();
  const { ensureSession } = useAgentChat();
  const { announce, setChatOpen } = useShell();
  const { events } = useLive();
  const router = useRouter();
  const queryClient = useQueryClient();
  const wid = workspace.id;

  const samples = useQuery({ queryKey: ["samples", wid], queryFn: () => api.documents.samples(wid) });
  const proposals = useQuery({ queryKey: ["proposals", wid, "PENDING"], queryFn: () => api.proposals.list(wid) });
  const questions = useQuery({ queryKey: ["questions", wid, "OPEN"], queryFn: () => api.questions.list(wid) });
  const history = useQuery({ queryKey: ["proposals", wid, "ALL"], queryFn: () => api.proposals.list(wid, "ALL") });
  const proposal = proposals.data?.find((p) => p.proposal_type === "TEMPLATE_CHANGE");

  const [files, setFiles] = useState<File[]>([]);
  const [notes, setNotes] = useState("");
  const [confirmRestart, setConfirmRestart] = useState(false);
  // Sent the existing samples again: no new sample marks the request, so remember it here.
  const [restudying, setRestudying] = useState(false);
  const samplesPanel = useRef<HTMLDivElement>(null);
  const proposalTop = useRef<HTMLDivElement>(null);
  const [viewing, setViewing] = useState<number | null>(null);
  // Edits start from the agent's latest proposal; a newer proposal from the agent starts over.
  const [edits, setEdits] = useState<{ proposalId: string; schema: TemplateSchema } | null>(null);
  const [nameEdit, setName] = useState<string | null>(null);
  const proposed = proposal?.proposed_data.schema_definition;
  const draft = useMemo(
    () => (edits && edits.proposalId === proposal?.id ? edits.schema : proposed ? normalizeSchema(proposed) : null),
    [edits, proposal?.id, proposed],
  );
  const setDraft = (schema: TemplateSchema) => proposal && setEdits({ proposalId: proposal.id, schema });
  const name = nameEdit ?? ((workspace.status === "draft" && proposal?.proposed_data.name) || workspace.name);

  const studying = useMemo(() => {
    const started = events.filter((e) => e.event_type === "processing_started" && e.trigger === "template.samples_added");
    const last = started.at(-1);
    if (!last) return false;
    return !events.some((e) => e.event_type === "agent_run_finished" && e.run_id === last.run_id);
  }, [events]);

  const upload = useMutation({
    mutationFn: async () => api.template.discover(wid, files, notes, await ensureSession()),
    onSuccess: () => {
      if (files.length) {
        announce(`You uploaded ${files.length} sample${files.length === 1 ? "" : "s"} for the agent to study`, { openChat: true });
      } else {
        announce("You asked the agent to study the samples again", { openChat: true });
        setRestudying(true);
      }
      setFiles([]);
      setNotes("");
      queryClient.invalidateQueries({ queryKey: ["samples", wid] });
    },
  });

  const restart = useMutation({
    mutationFn: () => api.template.withdrawProposal(wid),
    onSuccess: () => {
      setConfirmRestart(false);
      setEdits(null);
      setName(null);
      setRestudying(false);
      ["proposals", "questions", "thread", "workspaces"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
      requestAnimationFrame(() => samplesPanel.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    },
  });

  const publish = useMutation({
    mutationFn: () => api.template.publish(wid, name.trim() || workspace.name, draft!, workspace.status === "draft"),
    onSuccess: async () => {
      const wasDraft = workspace.status === "draft";
      announce(wasDraft ? "You published the template. The workspace is ready for documents." : "You published a new template version.");
      await queryClient.refetchQueries({ queryKey: ["workspace"] });
      ["workspaces", "template", "proposals"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
      router.push(wasDraft ? href("documents") : href("template"));
    },
  });

  const hasSamples = (samples.data?.length ?? 0) > 0;
  const fieldCount = draft?.objects.reduce((n, o) => n + o.fields.length, 0) ?? 0;
  // Waiting for a proposal: the agent is at work, or samples arrived after the last proposal was settled.
  const lastSettled = (history.data ?? [])
    .filter((p) => p.proposal_type === "TEMPLATE_CHANGE" && p.status !== "PENDING")
    .reduce((t, p) => Math.max(t, Date.parse(p.reviewed_at ?? p.created_at)), 0);
  const newestSample = (samples.data ?? []).reduce((t, s) => Math.max(t, Date.parse(s.created_at)), 0);
  const awaiting =
    !proposal && (studying || restudying || (workspace.status === "draft" && hasSamples && newestSample > lastSettled));
  const canRestudy = hasSamples && !proposal && !awaiting;

  // When the proposal lands while you wait, bring it into view.
  const wasAwaiting = useRef(false);
  useEffect(() => {
    if (proposal && wasAwaiting.current) proposalTop.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    wasAwaiting.current = awaiting;
  }, [awaiting, proposal]);

  const showPicker = !proposal && !awaiting;
  const sampleImages = (samples.data ?? []).map((s) => ({ src: api.documents.fileUrl(wid, s.id), title: s.original_filename }));
  const designActions = (
    <>
      <Button variant="primary" disabled={publish.isPending || fieldCount === 0} onClick={() => publish.mutate()}>
        {publish.isPending ? "Publishing…" : "Publish template"}
      </Button>
      <Button variant="secondary" disabled={publish.isPending} onClick={() => setConfirmRestart(true)}>
        Ask for a new proposal
      </Button>
    </>
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={workspace.status === "draft" ? "Design the template" : "Refine the template"}
        subtitle={
          proposal
            ? `The agent proposes ${draft?.objects.length ?? 0} object${draft?.objects.length === 1 ? "" : "s"} with ${fieldCount} fields. Edit anything, then publish.`
            : studying
              ? "The agent is studying your samples. Follow along in the chat."
              : awaiting
                ? "Waiting for the agent’s proposal."
                : workspace.status === "draft" && hasSamples
                  ? "Upload new samples, or send these again, and the agent proposes from scratch."
                  : workspace.status === "draft"
                    ? "Upload a few typical documents. The agent works out what to capture from them."
                    : "Upload new samples when your documents change. The agent proposes what to update."
        }
        actions={
          <>
            {draft && designActions}
            <Button variant="secondary" className="xl:hidden" onClick={() => setChatOpen(true)}>
              Open the chat
            </Button>
          </>
        }
      />

      {(questions.data?.length ?? 0) > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-card bg-[var(--pill-info-bg)] px-4 py-3 text-body text-[var(--pill-info-ink)]">
          <p className="min-w-0 flex-1">
            Syruss has {questions.data!.length} question{questions.data!.length === 1 ? "" : "s"} for you. Your answers update the proposal.
          </p>
          <Button size="sm" variant="secondary" onClick={() => setChatOpen(true, "activity")}>Answer in Activity</Button>
        </div>
      )}

      {/* Once sent, the samples stay in view (open any of them full size) while the agent works. */}
      {!showPicker && hasSamples && (
        <Panel title={awaiting ? "The agent is studying these samples" : "Samples"} aside={`${samples.data!.length} uploaded`}>
          <SampleStrip images={sampleImages} onOpen={setViewing} />
        </Panel>
      )}

      {draft ? (
        <div ref={proposalTop} className="flex scroll-mt-6 flex-col gap-6">
          <Field label="Workspace name">
            {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} className="max-w-md" />}
          </Field>
          <SchemaEditor schema={draft} onChange={setDraft} />
          <div className="flex flex-wrap items-center gap-3">
            {designActions}
            <span className="text-body text-muted">
              {workspace.status === "draft" ? "Documents can be processed once it's published." : "Becomes the active version."}
            </span>
          </div>
          {publish.error && <p className="text-body text-negative">{publish.error.message}</p>}
        </div>
      ) : awaiting ? (
        // The proposal's own layout, with bars where its objects and fields will be.
        <div className="flex flex-col gap-4 rounded-sheet bg-well p-2 sm:p-3" aria-busy>
          <p className="px-2 pt-1 text-body text-muted">The proposal will appear here when the agent is done.</p>
          {[3, 2].map((rows, i) => (
            <div key={i} className="rounded-card border border-border bg-card">
              <div className="flex flex-col gap-2 border-b border-border px-4 py-3">
                <Bar className="h-4 w-32" />
                <Bar className="h-3 w-48" />
              </div>
              <div className="flex flex-col gap-3 px-4 py-3">
                {Array.from({ length: rows }, (_, r) => (
                  <Bar key={r} className="h-8 w-full" />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {showPicker && (
        <div ref={samplesPanel} className="scroll-mt-6">
          <Panel title="Samples" aside={hasSamples ? `${samples.data!.length} uploaded` : undefined}>
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (files.length || canRestudy) upload.mutate();
              }}
            >
              <Dropzone files={files} onChange={setFiles} max={5} hint="1 to 5 typical documents. Clear photos or scans work best." />
              <Field label="Notes for the agent (optional)" help="Anything it can't see on the page: what the documents are for, codes your team uses.">
                {(id) => <Textarea id={id} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} />}
              </Field>
              {upload.error && <p className="text-body text-negative">{upload.error.message}</p>}
              <div>
                <Button type="submit" variant="primary" disabled={!(files.length || canRestudy) || upload.isPending}>
                  {upload.isPending
                    ? "Sending…"
                    : !hasSamples
                      ? "Upload samples"
                      : !files.length && canRestudy
                        ? "Study these samples again"
                        : "Upload and propose"}
                </Button>
              </div>
            </form>
            {hasSamples && (
              <div className="mt-4">
                <SampleStrip images={sampleImages} onOpen={setViewing} />
              </div>
            )}
          </Panel>
        </div>
      )}

      {workspace.status !== "draft" && (
        <p className="text-body text-muted">
          The current template is on the <Link href={href("template")} className="text-ink underline underline-offset-2">Template</Link> page.
        </p>
      )}

      <ImageViewer images={sampleImages} index={viewing} onIndexChange={setViewing} />
      <Dialog
        open={confirmRestart}
        onOpenChange={(open) => !restart.isPending && setConfirmRestart(open)}
        title="Start the design over?"
        description={`The agent’s proposal and your edits to it are cleared${
          workspace.status === "draft" ? ", and its open questions about it are closed" : ""
        }. Then upload new samples, or send the current ones again, and the agent proposes from scratch.`}
      >
        {restart.error && <p className="mb-3 text-body text-negative">{restart.error.message}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" disabled={restart.isPending} onClick={() => setConfirmRestart(false)}>
            Keep it
          </Button>
          <Button variant="primary" disabled={restart.isPending} onClick={() => restart.mutate()}>
            {restart.isPending ? "Clearing…" : "Clear the proposal"}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

/** Sample pages as thumbnails; each opens the viewer at that page. */
function SampleStrip({ images, onOpen }: { images: ViewerImage[]; onOpen: (index: number) => void }) {
  return (
    <ul className="flex flex-wrap gap-2">
      {images.map((img, i) => (
        <li key={img.src} className="animate-rise" style={{ animationDelay: `${i * 40}ms` }}>
          <button
            type="button"
            onClick={() => onOpen(i)}
            title={img.title}
            aria-label={`Open ${img.title}`}
            className="block size-20 overflow-hidden rounded-control border border-border bg-raised transition-[transform,border-color] duration-150 hover:-translate-y-0.5 hover:border-control active:translate-y-0 sm:size-24"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={img.src} alt="" loading="lazy" className="size-full object-cover" />
          </button>
        </li>
      ))}
    </ul>
  );
}
