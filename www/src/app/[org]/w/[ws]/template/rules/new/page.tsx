"use client";

import { Sparkles } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useAgentChat } from "@/components/chat/ChatProvider";
import { PageHeader, Tabs } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { RuleEditor } from "@/components/rules/RuleEditor";
import { useWorkspace } from "@/lib/workspace";

const EXAMPLES = [
  "The line amounts must add up to the invoice total",
  "No shift can be longer than 16 hours",
  "The number of guards listed must match the guard count written on the roster",
  "Every employee number appears only once per roster",
];

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
        { href: href("template/rules/new"), label: "Describe it", active: !custom },
        { href: href("template/rules/new?mode=custom"), label: "Write it myself", active: custom },
      ]} />
      {custom ? <RuleEditor /> : <Describe />}
    </div>
  );
}

/** Say what the rule should check in plain words: it goes to Syruss in a new chat, which writes it, tries it and offers it to add. */
function Describe() {
  const { sendInNewChat } = useAgentChat();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string>();
  const go = async (value: string) => {
    const message = value.trim();
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
  return (
    <form className="flex max-w-3xl flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void go(text); }}>
      <label htmlFor="rule-words" className="text-body font-medium text-ink">Describe the rule in plain English</label>
      <Textarea
        id="rule-words" value={text} rows={4} maxLength={2000} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void go(text); }}
        placeholder="For example: the hours of each shift must match the time between in and out, give or take 15 minutes"
      />
      <p className="text-meta text-muted">
        Syruss writes the rule in a new chat, tries it on your saved records and shows you what it would have caught. You add it with one click.
      </p>
      <div className="flex flex-wrap gap-1.5">
        {EXAMPLES.map((e) => (
          <button key={e} type="button" onClick={() => setText(e)} className="rounded-chip bg-raised px-2 py-1 text-meta text-muted hover:text-ink">{e}</button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={!text.trim() || sending}>
          <Sparkles aria-hidden />{sending ? "Sending…" : "Ask Syruss to write it"}
        </Button>
        {sent && <p className="text-body text-muted" role="status">Sent. Syruss is working on it in the chat.</p>}
        {error && <p className="text-body text-negative">{error}</p>}
      </div>
    </form>
  );
}
