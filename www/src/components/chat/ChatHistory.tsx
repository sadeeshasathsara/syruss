"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bar } from "@/components/ui/Blocks";
import { IconButton } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";
import { useAgentChat } from "./ChatProvider";

const WIDTH = 288;
const GAP = 8;

/** Where the popup goes: beside the chat panel on its left, the same height; over it when there's no room. */
function usePlacement(anchor: React.RefObject<HTMLElement | null>, open: boolean) {
  const [rect, setRect] = useState<{ top: number; left: number; width: number; height: number } | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const panel = anchor.current?.closest<HTMLElement>("[data-chat-panel]") ?? anchor.current?.parentElement;
    if (!panel) return;
    const place = () => {
      const r = panel.getBoundingClientRect();
      const beside = r.left >= WIDTH + GAP * 2;
      setRect(beside
        ? { top: r.top, left: r.left - GAP - WIDTH, width: WIDTH, height: r.height }
        : { top: r.top, left: r.left, width: r.width, height: r.height });
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(panel);
    window.addEventListener("resize", place);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", place);
    };
  }, [anchor, open]);
  return rect;
}

/**
 * Your earlier chats. Nothing is fetched until it opens; then titles arrive 20 at a time as you
 * scroll. A chat's messages load only when you open it.
 */
export function ChatHistory({ open, onClose, anchor }: { open: boolean; onClose: () => void; anchor: React.RefObject<HTMLElement | null> }) {
  const { workspace } = useWorkspace();
  const { activeId, selectSession } = useAgentChat();
  const rect = usePlacement(anchor, open);
  const list = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const history = useInfiniteQuery({
    queryKey: ["conversations", workspace.id, "history"],
    queryFn: ({ pageParam }) => api.chat.list(workspace.id, { kind: "history", offset: pageParam, limit: 20 }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.next_offset : undefined),
    enabled: open,
  });
  const items = history.data?.pages.flatMap((p) => p.items) ?? [];

  // Load the next page when the end of the list scrolls into view.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = history;
  useEffect(() => {
    if (!open || !sentinel.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => entry.isIntersecting && hasNextPage && !isFetchingNextPage && fetchNextPage(),
      { root: list.current, rootMargin: "120px" },
    );
    observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [open, hasNextPage, isFetchingNextPage, fetchNextPage, items.length]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !rect) return null;
  return createPortal(
    <>
      <div className="fixed inset-0 z-40" aria-hidden onClick={onClose} />
      <div
        role="dialog"
        aria-label="Chat history"
        style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
        className="fixed z-50 flex animate-fade flex-col overflow-hidden rounded-sheet border border-border bg-chat-panel shadow-float"
      >
        <div className="flex h-12 flex-none items-center gap-2 border-b border-border pl-4 pr-1.5">
          <h2 className="min-w-0 flex-1 text-body font-semibold text-ink">Chat history</h2>
          <IconButton aria-label="Close chat history" onClick={onClose}><X /></IconButton>
        </div>
        <div ref={list} className="min-h-0 flex-1 overflow-y-auto p-1.5 scrollbar-thin">
          {history.isLoading ? (
            <div className="flex flex-col gap-2 p-1.5">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-9 w-full" />)}</div>
          ) : history.isError ? (
            <p className="p-3 text-body text-negative">Couldn&rsquo;t load your chats.</p>
          ) : items.length === 0 ? (
            <p className="p-3 text-body text-muted">No earlier chats yet.</p>
          ) : (
            <ul className="flex flex-col">
              {items.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => {
                      selectSession(c.id);
                      onClose();
                    }}
                    className={cn(
                      "flex w-full cursor-pointer items-center gap-2 rounded-control px-2.5 py-2 text-left transition-colors hover:bg-raised",
                      c.id === activeId && "bg-raised",
                    )}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body text-ink">{c.title}</span>
                      <span className="block text-meta text-muted">
                        {c.kind !== "chat" ? `${c.kind === "flag" ? "Decision" : "Started by Syruss"} · ${c.status === "resolved" ? "resolved" : "pending"} · ` : ""}{ago(c.updated_at)}
                      </span>
                    </span>
                    {c.id === activeId && <Check className="size-4 flex-none text-ink" />}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div ref={sentinel} className="h-1" />
          {isFetchingNextPage && <Bar className="mx-1.5 my-2 h-9" />}
        </div>
      </div>
    </>,
    document.body,
  );
}
