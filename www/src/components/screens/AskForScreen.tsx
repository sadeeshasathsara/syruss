"use client";

import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useAgentChat } from "@/components/chat/ChatProvider";
import { useShell } from "@/components/shell/ShellContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";

/**
 * Ask Syruss to build or change a screen in plain words. It goes to the chat, and Syruss works as the analyst: it looks at
 * the records, tries the screen, and saves it. The person is told in the chat and the new screen appears in the sidebar.
 */
export function AskForScreen({ screen, placeholder, examples }: { screen?: string; placeholder: string; examples?: string[] }) {
  const { send } = useAgentChat();
  const { setChatOpen } = useShell();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const go = async (message: string) => {
    const value = message.trim();
    if (!value || sending) return;
    setSending(true);
    setChatOpen(true);
    try {
      await send(value, { mode: "screens", ...(screen ? { screen } : {}) });
      setText("");
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="flex flex-col gap-2">
      <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void go(text); }}>
        <label className="relative min-w-0 flex-1 basis-64">
          <Sparkles aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
          <span className="sr-only">Ask Syruss</span>
          <Input className="pl-9" value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} />
        </label>
        <Button type="submit" variant="secondary" disabled={!text.trim() || sending}>{sending ? "Asking…" : "Ask Syruss"}</Button>
      </form>
      {examples && examples.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {examples.map((e) => (
            <button key={e} type="button" onClick={() => go(e)} className="rounded-chip bg-raised px-2 py-1 text-meta text-muted hover:text-ink">{e}</button>
          ))}
        </div>
      )}
    </div>
  );
}
