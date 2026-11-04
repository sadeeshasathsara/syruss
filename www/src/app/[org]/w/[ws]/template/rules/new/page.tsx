"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRight, SendHorizontal } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { useAgentChat } from "@/components/chat/ChatProvider";
import { Bar, PageHeader, Tabs } from "@/components/ui/Blocks";
import { RuleEditor } from "@/components/rules/RuleEditor";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

export default function NewRulePage() {
  return (
    <Suspense>
      <NewRule />
    </Suspense>
  );
}

function NewRule() {
  const { href } = useWorkspace();
  const custom = useSearchParams().get("mode") === "custom";
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="New rule" subtitle="A check every record goes through after its document is read." back={{ href: href("template/rules"), label: "Rules" }} />
      <Tabs items={[
        { href: href("template/rules/new"), label: "Describe", active: !custom },
        { href: href("template/rules/new?mode=custom"), label: "Write the expression", active: custom },
      ]} />
      {custom ? <RuleEditor /> : <Describe />}
    </div>
  );
}

/** Say what the rule should check in plain words: it goes to Syruss in a new chat, which writes it, tries it and offers it to add. */
function Describe() {
  const { workspace } = useWorkspace();
  const { sendInNewChat } = useAgentChat();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string>();
  const input = useRef<HTMLTextAreaElement>(null);

  const go = async () => {
    const message = text.trim();
    if (!message || sending) return;
    setSending(true);
    setError(undefined);
    try {
      await sendInNewChat(message, { mode: "rules" });
      setText("");
      setSent(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't send it to Syruss.");
    } finally {
      setSending(false);
    }
  };
  const pick = (idea: string) => {
    setText(idea);
    setSent(false);
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.setSelectionRange(idea.length, idea.length);
    });
  };

  return (
    <div className="grid max-w-5xl grid-cols-1 items-start gap-8 @4xl:grid-cols-[minmax(0,1fr)_20rem]">
      <form className="flex min-w-0 flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void go(); }}>
        <label htmlFor="rule-words" className="text-body font-medium text-ink">What should every record satisfy?</label>
        <div className="flex flex-col rounded-card border border-control bg-sheet focus-within:border-accent">
          <textarea
            id="rule-words" ref={input} value={text} rows={4} maxLength={2000}
            onChange={(e) => { setText(e.target.value); setSent(false); }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void go(); } }}
            placeholder="Describe the check in your own words"
            className="min-h-24 resize-y bg-transparent px-3 pt-2.5 text-body text-ink placeholder:text-faint focus:outline-none"
          />
          <div className="flex items-center justify-between gap-3 px-3 pb-2 pt-1">
            <span className="text-meta text-faint">Enter to send, Shift+Enter for a new line</span>
            <button
              type="submit" aria-label="Send to Syruss" title="Send to Syruss" disabled={!text.trim() || sending}
              className="flex size-8 flex-none items-center justify-center rounded-control bg-primary text-primary-ink transition-opacity disabled:opacity-40 pointer-coarse:size-10"
            >
              <SendHorizontal className="size-4" />
            </button>
          </div>
        </div>
        <p className="text-meta text-muted" role="status">
          {error ? <span className="text-negative">{error}</span>
            : sent ? "Sent. Syruss is writing the rule in the chat; you add it from there."
            : "Syruss writes the rule in a new chat and tries it on your saved records. Nothing is added until you approve it."}
        </p>
      </form>

      <Ideas workspaceId={workspace.id} workspaceName={workspace.name} onPick={pick} />
    </div>
  );
}

/** Rules Syruss thinks are worth having for this template (once per template version), as starting points. */
function Ideas({ workspaceId, workspaceName, onPick }: { workspaceId: string; workspaceName: string; onPick: (idea: string) => void }) {
  const ideas = useQuery({
    queryKey: ["rules", workspaceId, "ideas"],
    queryFn: () => api.rules.ideas(workspaceId),
    // While Syruss is thinking, look again now and then (asking again never wakes it twice).
    refetchInterval: (q) => (q.state.data?.status === "thinking" ? 10_000 : false),
  });
  const status = ideas.data?.status;
  if (ideas.isError || status === "none" || status === "no_template") return null;
  if (status === "ready" && ideas.data!.ideas.length === 0) return null;

  return (
    <section aria-labelledby="rule-ideas" className="flex flex-col gap-2">
      <h2 id="rule-ideas" className="text-body font-medium text-ink">Ideas for {workspaceName}</h2>
      {status === "ready" ? (
        <ul className="flex flex-col divide-y divide-rule overflow-hidden rounded-card border border-border">
          {ideas.data!.ideas.map((idea) => (
            <li key={idea.text}>
              <button
                type="button" onClick={() => onPick(idea.text)}
                className="group flex w-full items-center gap-3 bg-sheet px-3 py-2.5 text-left text-body text-ink transition-colors hover:bg-raised"
              >
                <span className="min-w-0 flex-1">{idea.text}</span>
                <ChevronRight aria-hidden className="size-4 flex-none text-faint transition-colors group-hover:text-ink" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex flex-col gap-2" aria-busy="true">
          {[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}
          <p className="text-meta text-muted">Syruss is looking at your template and records.</p>
        </div>
      )}
    </section>
  );
}
