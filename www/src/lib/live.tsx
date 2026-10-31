"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { liveStreamUrl } from "./api";
import type { LiveEvent } from "./types";

type Status = "connecting" | "live" | "offline";

interface LiveContextValue {
  status: Status;
  events: LiveEvent[];
  subscribe: (handler: (e: LiveEvent) => void) => () => void;
  /** Listen to one more channel (a chat you opened) until the returned function is called. */
  watch: (channel: string) => () => void;
}

const LiveContext = createContext<LiveContextValue | null>(null);

const MAX_EVENTS = 600;

/** Which cached lists an event makes stale. */
function staleKeys(e: LiveEvent): string[][] {
  switch (e.event_type) {
    case "agent_completed":
    case "document_status":
      return [["documents"], ["reviews"], ["workspace"], ["extraction"], ["screen-run"]];
    case "screen_changed":
      return [["screens"], ["screen"], ["screen-run"]];
    case "agent_question":
      return [["questions"], ["workspace"]];
    case "agent_proposal":
    case "template_discovered":
      return [["proposals"], ["sources"], ["workspace"]];
    case "agent_message":
      return [["thread"]];
    case "agent_conversation":
      return [["conversations"], ["conversation"], ["workspace"], ["questions"]];
    case "intake_received":
      return [["channels"], ["connections"]];
    case "delivery_changed":
      return [["deliveries"], ["destinations"], ["connections"]];
    case "agent_run_finished":
      return [["documents"], ["entities"], ["sources"], ["runs"], ["workspace"], ["screens"], ["screen-run"]];
    default:
      return [];
  }
}

const POLLED = new Set(["screens", "documents", "reviews", "workspace", "extraction", "questions", "proposals", "sources", "thread", "conversations", "conversation", "runs", "entities", "channels", "connections", "deliveries", "destinations"]);

type Link = { onOpen: () => void; onEvent: (raw: LiveEvent) => void; onDown: () => void };

/**
 * Opens one live channel and keeps it open: the browser reconnects by itself after a dropped connection, and this
 * starts over (slowly) when the API refuses the channel or the sign-in has ended.
 */
function openChannel(channel: string, link: Link): () => void {
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;

  const connect = () => {
    source = new EventSource(liveStreamUrl(channel));
    source.onopen = () => link.onOpen();
    source.onmessage = (msg) => {
      try {
        link.onEvent(JSON.parse(msg.data));
      } catch {
        /* not an event */
      }
    };
    source.onerror = () => {
      link.onDown();
      // Still CONNECTING: the browser is retrying on its own. CLOSED: refused (not yours to hear, or signed out).
      if (closed || source?.readyState !== EventSource.CLOSED) return;
      console.warn(`Live updates: the ${channel.split(":")[0]} channel was refused or closed; trying again shortly`);
      timer = setTimeout(connect, 30_000);
    };
  };
  connect();
  return () => {
    closed = true;
    clearTimeout(timer);
    source?.close();
  };
}

/**
 * One live connection per workspace, on the workspace's own channel, plus the channels of the chats you
 * have open (a private chat's work is only ever sent to its own channel). Keeps an ordered log of events
 * for the chat and refreshes the lists an event changes.
 */
export function LiveProvider({ workspaceId, children }: { workspaceId: string; children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("connecting");
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const handlers = useRef(new Set<(e: LiveEvent) => void>());
  const queryClient = useQueryClient();
  const seen = useRef(new Set<string>());
  const watched = useRef(new Map<string, { count: number; close: () => void }>());

  const onEvent = useCallback((raw: LiveEvent) => {
    if (raw.event_type === "connected") return;
    const event = raw.data && typeof raw.data === "object" ? { ...(raw.data as object), ...raw } as LiveEvent : raw;
    // The server replays recent history on connect; skip anything already seen.
    const key = `${event.event_type}|${event.timestamp}|${event.run_id ?? ""}|${event.step ?? ""}|${event.tool_name ?? ""}|${event.message_id ?? event.id ?? ""}`;
    if (seen.current.has(key)) return;
    seen.current.add(key);
    setEvents((prev) => (prev.length >= MAX_EVENTS ? [...prev.slice(-MAX_EVENTS + 1), event] : [...prev, event]));
    handlers.current.forEach((h) => h(event));
    staleKeys(event).forEach((k) =>
      // The workspace summary is keyed by slug, so it is refreshed by prefix.
      queryClient.invalidateQueries({ queryKey: k[0] === "workspace" ? ["workspace"] : [...k, workspaceId] }),
    );
  }, [queryClient, workspaceId]);

  useEffect(() => {
    seen.current = new Set();
    return openChannel(`template:${workspaceId}`, {
      onOpen: () => setStatus("live"),
      onEvent,
      onDown: () => setStatus((s) => (s === "live" ? "offline" : s)),
    });
  }, [workspaceId, onEvent]);

  // Without a live connection (blocked network, a restart) the lists it would refresh are polled instead, so the
  // work still shows up, only a few seconds later.
  useEffect(() => {
    if (status === "live") return;
    const timer = setInterval(() => {
      queryClient.invalidateQueries({ predicate: (q) => POLLED.has(String(q.queryKey[0])) });
    }, 4000);
    return () => clearInterval(timer);
  }, [status, queryClient]);

  // Chats you open: their channel carries their replies and the work done for them, for you alone.
  useEffect(() => {
    const open = watched.current;
    return () => {
      open.forEach((w) => w.close());
      open.clear();
    };
  }, [workspaceId]);

  const watch = useCallback((channel: string) => {
    const open = watched.current;
    const existing = open.get(channel);
    if (existing) existing.count += 1;
    else open.set(channel, { count: 1, close: openChannel(channel, { onOpen: () => {}, onEvent, onDown: () => {} }) });
    return () => {
      const w = open.get(channel);
      if (!w) return;
      w.count -= 1;
      if (w.count <= 0) {
        w.close();
        open.delete(channel);
      }
    };
  }, [onEvent]);

  const subscribe = useCallback((handler: (e: LiveEvent) => void) => {
    handlers.current.add(handler);
    return () => {
      handlers.current.delete(handler);
    };
  }, []);

  return <LiveContext.Provider value={{ status, events, subscribe, watch }}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveContextValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside a workspace");
  return ctx;
}
