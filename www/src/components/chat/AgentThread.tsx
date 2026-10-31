"use client";

import { ComposerPrimitive, MessagePrimitive, ThreadPrimitive, type DataMessagePartProps } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { ArrowUp, History, Pencil, Square, SquarePen, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { useShell } from "@/components/shell/ShellContext";
import { Button, IconButton } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { markdownComponents, remarkPlugins } from "@/components/ui/Markdown";
import { Pill } from "@/components/ui/Pill";
import { Dialog } from "@/components/ui/Sheet";
import { cn } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useWorkspace } from "@/lib/workspace";
import { ActivityFeed } from "./Activity";
import { ChatHistory } from "./ChatHistory";
import { TimelineCard } from "./cards";
import { useAgentChat } from "./ChatProvider";
import type { TimelineItem } from "./timeline";

function MarkdownText() {
  return <MarkdownTextPrimitive remarkPlugins={remarkPlugins} components={markdownComponents} className="md text-body text-ink" />;
}

function DataCard(props: DataMessagePartProps<TimelineItem>) {
  return <TimelineCard data={props.data} />;
}

const parts = {
  Text: MarkdownText,
  data: { Fallback: DataCard },
};

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex animate-rise justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-card rounded-br-chip bg-bubble px-3 py-2 text-body text-ink">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="min-w-0 animate-rise">
      <MessagePrimitive.Parts components={parts} />
    </MessagePrimitive.Root>
  );
}

function Suggestions() {
  const { workspace } = useWorkspace();
  const { send } = useAgentChat();
  const prompts =
    workspace.status === "draft"
      ? ["What do you need from me to design this template?", "Which sample documents work best?"]
      : ["What needs my attention?", "Summarise today's documents", "Which fields do you find hardest to read?"];
  return (
    <div className="flex flex-col items-start gap-2">
      {prompts.map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => send(p)}
          className="rounded-control border border-border bg-card px-3 py-1.5 text-left text-body text-ink transition-colors hover:border-control"
        >
          {p}
        </button>
      ))}
    </div>
  );
}

/** Which conversation you're in, your chat history beside the panel, and new, rename or delete. */
function SessionBar() {
  const { active, activeId, newSession, renameSession, deleteSession } = useAgentChat();
  const bar = useRef<HTMLDivElement>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const issue = active?.kind === "agent" || active?.kind === "flag";
  const current = activeId && active && !issue ? active : undefined;

  return (
    <div ref={bar} className="flex h-11 flex-none items-center gap-1 border-b border-border bg-chat-panel px-2">
      <IconButton aria-label="Your chats" aria-expanded={historyOpen} onClick={() => setHistoryOpen((o) => !o)}>
        <History />
      </IconButton>
      <span className="min-w-0 flex-1 truncate px-1 text-body font-medium text-ink">
        {activeId ? active?.title ?? "" : "New chat"}
      </span>
      {issue && <Pill tone={active?.status === "resolved" ? "positive" : "info"}>{active?.status === "resolved" ? "Resolved" : "Open"}</Pill>}
      <IconButton aria-label="New chat" onClick={newSession}><SquarePen /></IconButton>
      {current && (
        <>
          <IconButton aria-label="Rename this chat" onClick={() => setRenaming(current.title)}><Pencil /></IconButton>
          <IconButton aria-label="Delete this chat" onClick={() => setConfirmDelete(true)}><Trash2 /></IconButton>
        </>
      )}
      <ChatHistory open={historyOpen} onClose={() => setHistoryOpen(false)} anchor={bar} />

      <Dialog open={renaming !== null} onOpenChange={(o) => !o && setRenaming(null)} title="Rename this chat">
        <form
          className="flex flex-col gap-4"
          onSubmit={async (e) => {
            e.preventDefault();
            if (current && renaming?.trim()) await renameSession(current.id, renaming.trim());
            setRenaming(null);
          }}
        >
          <Input aria-label="Chat name" value={renaming ?? ""} onChange={(e) => setRenaming(e.target.value)} maxLength={120} autoFocus />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRenaming(null)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={!renaming?.trim()}>Rename</Button>
          </div>
        </form>
      </Dialog>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete} title="Delete this chat?" description="Its messages are deleted. Work it started keeps its results and stays in Activity.">
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Keep it</Button>
          <Button
            variant="danger"
            onClick={async () => {
              if (current) await deleteSession(current.id);
              setConfirmDelete(false);
            }}
          >
            Delete
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

/** A quiet pointer from the chat to what's waiting in Activity. */
/** A quiet pointer from the chat to what's waiting on you: Review first, then the agent inbox. */
function WaitingNotice() {
  const { workspace, href } = useWorkspace();
  const review = workspace.needs_review + workspace.open_questions;
  const inbox = (workspace.open_conversations ?? 0) + workspace.pending_proposals;
  const waiting = review + inbox;
  if (!waiting) return null;
  return (
    <Link
      href={href(review ? "review" : "agent")}
      className="mb-2 flex w-full animate-fade items-center gap-2 rounded-control bg-[var(--pill-info-bg)] px-3 py-2 text-left text-body text-[var(--pill-info-ink)] transition-opacity hover:opacity-90"
    >
      <span className="min-w-0 flex-1">Syruss has {waiting} thing{waiting === 1 ? "" : "s"} waiting on you</span>
      <span className="flex-none font-medium">{review ? "Open Review" : "Open agent inbox"}</span>
    </Link>
  );
}

function ChatView() {
  const { workspace } = useWorkspace();
  const { status } = useLive();
  const { replying, stop, stopping } = useAgentChat();
  return (
    <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
      <SessionBar />
      {/* The thread sits on a well so each card, message and action line reads as its own thing. */}
      <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 scroll-smooth flex-col gap-3 overflow-y-auto bg-chat px-3 py-4 scrollbar-thin motion-reduce:scroll-auto sm:px-4">
        <ThreadPrimitive.Empty>
          <div className="flex flex-col gap-3">
            <p className="text-body text-muted">
              {workspace.status === "draft"
                ? "Hi, I’m Syruss. I design this workspace’s template with you. Upload sample documents and I’ll show you what I find, ask about anything unclear, and propose what to capture."
                : `Hi, I’m Syruss. Ask me about ${workspace.name}: its documents, records, people and what needs attention. I can also start reading documents for you.`}
            </p>
            <Suggestions />
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
      </ThreadPrimitive.Viewport>

      <div className="flex-none border-t border-border p-3">
        <WaitingNotice />
        <ComposerPrimitive.Root className="flex items-end gap-2 rounded-card border border-control bg-sheet p-1.5 focus-within:border-accent">
          <ComposerPrimitive.Input
            rows={1}
            autoFocus={false}
            submitOnEnter={!replying}
            placeholder="Ask Syruss or tell it something about your business"
            className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-2 py-1.5 text-body text-ink placeholder:text-faint focus:outline-none"
          />
          {replying ? (
            <button
              type="button"
              aria-label="Stop Syruss"
              title="Stop Syruss"
              disabled={stopping}
              onClick={() => stop()}
              className="flex size-8 flex-none animate-fade cursor-pointer items-center justify-center rounded-control bg-primary text-primary-ink disabled:opacity-40 pointer-coarse:size-10"
            >
              <Square className="size-3.5 fill-current" />
            </button>
          ) : (
            <ComposerPrimitive.Send
              aria-label="Send"
              className="flex size-8 flex-none animate-fade items-center justify-center rounded-control bg-primary text-primary-ink disabled:opacity-40 pointer-coarse:size-10"
            >
              <ArrowUp className="size-4" />
            </ComposerPrimitive.Send>
          )}
        </ComposerPrimitive.Root>
        {status !== "live" && (
          <p className="mt-2 text-meta text-muted">{status === "connecting" ? "Connecting to live updates…" : "Live updates paused. Reconnecting…"}</p>
        )}
      </div>
    </ThreadPrimitive.Root>
  );
}

/** Chat and Activity, for the panel's header. */
export function ChatTabs() {
  const { chatTab, setChatTab } = useShell();
  // What's waiting on you is counted in the navigation (Review, Agent inbox), not on these tabs.
  const tabs: { key: "chat" | "activity"; label: string; count?: number }[] = [
    { key: "chat", label: "Chat" },
    { key: "activity", label: "Activity" },
  ];
  return (
    <div role="tablist" aria-label="Syruss AI" className="flex items-center gap-0.5 rounded-control bg-active/60 p-0.5">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={chatTab === t.key}
            onClick={() => setChatTab(t.key)}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded-[5px] px-2.5 text-body transition-colors pointer-coarse:h-9",
              chatTab === t.key ? "bg-card font-medium text-ink shadow-sm" : "text-muted hover:text-ink",
            )}
          >
            {t.label}
            {!!t.count && (
              <span className="num flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--pill-info-bg)] px-1 text-[11px] font-semibold text-[var(--pill-info-ink)]">
                {t.count}
              </span>
            )}
          </button>
        ))}
    </div>
  );
}

/** The panel's body: your conversation with Syruss, or the workspace's activity. */
export function AgentThread() {
  const { chatTab } = useShell();
  return <div className="flex min-h-0 flex-1 flex-col">{chatTab === "chat" ? <ChatView /> : <ActivityFeed />}</div>;
}
