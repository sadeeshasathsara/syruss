"use client";

import { AssistantRuntimeProvider, useExternalStoreRuntime, type AppendMessage, type ThreadMessageLike } from "@assistant-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useShell } from "@/components/shell/ShellContext";
import { api, ApiError, setActiveConversation } from "@/lib/api";
import { storeValue, useStoredValue } from "@/lib/browser";
import { useLive } from "@/lib/live";
import type { Conversation } from "@/lib/types";
import { buildRuns, buildTimeline, toThreadMessages } from "./timeline";

interface ChatContextValue {
  /** The open conversation; undefined for a new chat not yet started. */
  activeId?: string;
  /** Its title, kind and status (loaded with its messages). */
  active?: Conversation;
  selectSession: (id: string | undefined) => void;
  /** Opens a conversation in the chat panel (e.g. one Syruss started, from the agent inbox). */
  openConversation: (id: string) => void;
  newSession: () => void;
  renameSession: (id: string, title: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  /** The open chat, created now if it doesn't exist yet (for work started from a page). */
  ensureSession: () => Promise<string>;
  /** `context` rides along with the message (for example { mode: "screens" } asks Syruss to work as the analyst). */
  send: (text: string, context?: Record<string, unknown>) => Promise<void>;
  busy: boolean;
  /** Syruss is answering your last message (or it is queued): the composer offers Stop. */
  replying: boolean;
  stop: () => Promise<void>;
  stopping: boolean;
  /** The newest piece of work in this chat: it shows open; older work folds away. */
  latestRunId?: string;
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function useAgentChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useAgentChat must be used inside a workspace");
  return ctx;
}

/**
 * Your private chats with Syruss. A chat holds your messages and Syruss's replies, the work you
 * started while it was open (from the chat or from a page) and notes of what you did. Everything
 * else in the workspace is in Activity. Held above the frame so it survives docking and sliding.
 */
export function ChatProvider({ workspaceId, children }: { workspaceId: string; children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const { events, watch } = useLive();
  const { notes, setChatOpen } = useShell();
  const [pending, setPending] = useState<{ id: string; text: string; at: string }[]>([]);
  const storeKey = `chat-session:${workspaceId}`;
  const stored = useStoredValue(storeKey);

  // The chat you had open last; nothing is listed until you open the history.
  const activeId = !stored || stored === "new" ? undefined : stored;

  // A private chat's replies and work arrive only on its own channel.
  useEffect(() => (activeId ? watch(activeId) : undefined), [activeId, watch]);

  useEffect(() => {
    setActiveConversation(activeId);
    return () => setActiveConversation(undefined);
  }, [activeId]);

  const conversation = useQuery({
    queryKey: ["conversation", workspaceId, activeId],
    queryFn: () => api.chat.get(workspaceId, activeId!),
    enabled: !!activeId,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });

  const selectSession = useCallback((id: string | undefined) => storeValue(storeKey, id ?? "new"), [storeKey]);
  // A chat that was deleted (or isn't yours) starts a new one instead.
  useEffect(() => {
    if (conversation.error instanceof ApiError && conversation.error.status === 404) selectSession(undefined);
  }, [conversation.error, selectSession]);
  const openConversation = useCallback((id: string) => {
    selectSession(id);
    setChatOpen(true, "chat");
  }, [selectSession, setChatOpen]);
  // Lists are paged and loaded only when shown; refresh whichever are cached.
  const refreshSessions = useCallback(() => queryClient.invalidateQueries({ queryKey: ["conversations", workspaceId] }), [queryClient, workspaceId]);

  const create = useMutation({ mutationFn: (title?: string) => api.chat.create(workspaceId, title) });
  const ensureSession = useCallback(async () => {
    if (activeId) return activeId;
    const conv = await create.mutateAsync(undefined);
    selectSession(conv.id);
    setActiveConversation(conv.id);
    await refreshSessions();
    return conv.id;
  }, [activeId, create, selectSession, refreshSessions]);

  const renameSession = useCallback(async (id: string, title: string) => {
    await api.chat.rename(workspaceId, id, title);
    await Promise.all([refreshSessions(), queryClient.invalidateQueries({ queryKey: ["conversation", workspaceId, id] })]);
  }, [workspaceId, refreshSessions, queryClient]);

  const deleteSession = useCallback(async (id: string) => {
    await api.chat.remove(workspaceId, id);
    if (id === activeId) selectSession(undefined);
    await refreshSessions();
  }, [workspaceId, activeId, selectSession, refreshSessions]);

  // Only what belongs to this chat: its runs (answers, and work started from it), its replies, its notes.
  const items = useMemo(() => {
    const mine = new Set(buildRuns(events).filter((r) => activeId && r.origin?.conversation_id === activeId).map((r) => r.id));
    const chatEvents = events.filter((e) =>
      e.run_id ? mine.has(e.run_id) : e.event_type === "agent_message" && !!activeId && e.conversation_id === activeId,
    );
    return buildTimeline({
      thread: conversation.data?.messages ?? [],
      pending,
      events: chatEvents,
      notes: notes.filter((n) => n.conversationId === activeId),
      questions: [],
      proposals: [],
    });
  }, [events, activeId, conversation.data, pending, notes]);

  const messages = useMemo(() => toThreadMessages(items), [items]);
  const busy = items.some((i) => i.kind === "run" && !i.run.finished) || items.some((i) => i.kind === "user" && i.pending);
  // Your last message has no answer yet (a reply, or a note that you stopped it), or its reply is running.
  const lastUser = items.findLastIndex((i) => i.kind === "user");
  const answered = items.slice(lastUser + 1).some((i) => i.kind === "agent" || (i.kind === "note" && i.text.endsWith("stopped Syruss.")));
  const replying =
    items.some((i) => i.kind === "run" && i.run.trigger === "human.message" && !i.run.finished) || (lastUser >= 0 && !answered);
  const [stopping, setStopping] = useState(false);
  const stop = useCallback(async () => {
    if (!activeId) return;
    setStopping(true);
    try {
      await api.chat.stop(workspaceId, activeId);
      await queryClient.invalidateQueries({ queryKey: ["conversation", workspaceId, activeId] });
    } finally {
      setStopping(false);
    }
  }, [activeId, workspaceId, queryClient]);
  // Answers to chat messages are shown as the reply itself, so they don't take the "newest work" slot.
  const latestRunId = items.findLast((i) => i.kind === "run" && i.run.trigger !== "human.message")?.id;

  const send = useCallback(
    async (text: string, context?: Record<string, unknown>) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const local = { id: `pending_${Date.now()}`, text: trimmed, at: new Date().toISOString() };
      setPending((p) => [...p, local]);
      try {
        const id = await ensureSession();
        await api.chat.send(workspaceId, id, trimmed, { current_page: pathname, ...context });
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["conversation", workspaceId, id] }),
          refreshSessions(), // the first message names the chat
        ]);
      } finally {
        setPending((p) => p.filter((x) => x.id !== local.id));
      }
    },
    [ensureSession, workspaceId, pathname, queryClient, refreshSessions],
  );

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    convertMessage: (m) => m,
    // Never "running" from the composer's point of view: you can always write to Syruss,
    // and your message queues behind its current work. Activity shows on the run cards.
    isRunning: false,
    isDisabled: !!activeId && conversation.isLoading,
    onNew: async (message: AppendMessage) => {
      const text = message.content
        .map((p) => (p.type === "text" ? p.text : ""))
        .join("\n");
      await send(text);
    },
  });

  return (
    <ChatContext.Provider
      value={{
        activeId, active: conversation.data, selectSession, openConversation, newSession: () => selectSession(undefined),
        renameSession, deleteSession,
        ensureSession, send, busy, latestRunId, replying, stop, stopping,
      }}
    >
      <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
    </ChatContext.Provider>
  );
}
