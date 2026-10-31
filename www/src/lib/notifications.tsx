"use client";

import { type InfiniteData, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, liveStreamUrl } from "./api";
import { armSound, playNotificationSound } from "./sound";
import type { AppNotification } from "./types";

type Page = { items: AppNotification[]; unread: number; has_more: boolean };
type Feed = InfiniteData<Page, string | undefined>;
const KEY = ["notifications"];

interface NotificationsValue {
  items: AppNotification[];
  unread: number;
  hasMore: boolean;
  loading: boolean;
  loadMore: () => void;
  setRead: (n: AppNotification, read: boolean) => void;
  readAll: () => void;
  remove: (n: AppNotification) => void;
  clear: (which: "all" | "read") => void;
  /** New notifications that arrived while you were here. */
  toasts: AppNotification[];
  dismissToast: (id: string) => void;
}

const Ctx = createContext<NotificationsValue | null>(null);

export function useNotifications(): NotificationsValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useNotifications must be used inside NotificationsProvider");
  return ctx;
}

/** Edits every loaded page of the feed, and the unread count on the first page. */
function updateFeed(feed: Feed | undefined, edit: (items: AppNotification[]) => AppNotification[], unread?: (n: number) => number): Feed | undefined {
  if (!feed) return feed;
  return {
    ...feed,
    pages: feed.pages.map((p, i) => ({ ...p, items: edit(p.items), unread: i === 0 && unread ? Math.max(0, unread(p.unread)) : p.unread })),
  };
}

/**
 * Your notifications across every workspace: loaded once, then kept live over your own
 * stream. Changes show at once and are saved behind.
 */
export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [toasts, setToasts] = useState<AppNotification[]>([]);
  const feed = useInfiniteQuery({
    queryKey: KEY,
    queryFn: ({ pageParam }) => api.notifications.list({ before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.has_more ? last.items.at(-1)?.created_at : undefined),
  });

  const dismissToast = useCallback((id: string) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  useEffect(() => armSound(), []);

  // Live: one stream for the signed-in person; the browser reconnects it by itself.
  useEffect(() => {
    const source = new EventSource(liveStreamUrl("notifications"));
    source.onopen = () => queryClient.invalidateQueries({ queryKey: KEY }); // catch up on anything missed while away
    source.onmessage = (msg) => {
      let event: { event_type?: string; data?: AppNotification };
      try {
        event = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (event.event_type !== "notification" || !event.data) return;
      const n = event.data;
      queryClient.setQueryData<Feed>(KEY, (old) =>
        old?.pages[0]?.items.some((x) => x.id === n.id)
          ? old
          : old && { ...old, pages: old.pages.map((p, i) => (i === 0 ? { ...p, items: [n, ...p.items], unread: p.unread + 1 } : p)) },
      );
      setToasts((t) => [n, ...t.filter((x) => x.id !== n.id)].slice(0, 3));
      playNotificationSound();
    };
    return () => source.close();
  }, [queryClient, dismissToast]);

  const patch = (edit: (items: AppNotification[]) => AppNotification[], unread?: (n: number) => number) =>
    queryClient.setQueryData<Feed>(KEY, (old) => updateFeed(old, edit, unread));
  const settle = () => queryClient.invalidateQueries({ queryKey: KEY });

  const read = useMutation({
    mutationFn: ({ n, value }: { n: AppNotification; value: boolean }) => api.notifications.setRead(n.id, value),
    onMutate: ({ n, value }) => {
      const now = new Date().toISOString();
      patch((items) => items.map((x) => (x.id === n.id ? { ...x, read_at: value ? x.read_at ?? now : null } : x)), (u) => u + (value ? (n.read_at ? 0 : -1) : n.read_at ? 1 : 0));
    },
    onError: settle,
  });
  const readAll = useMutation({
    mutationFn: api.notifications.readAll,
    onMutate: () => {
      const now = new Date().toISOString();
      patch((items) => items.map((x) => ({ ...x, read_at: x.read_at ?? now })), () => 0);
    },
    onError: settle,
  });
  const remove = useMutation({
    mutationFn: (n: AppNotification) => api.notifications.remove(n.id),
    onMutate: (n) => patch((items) => items.filter((x) => x.id !== n.id), (u) => u - (n.read_at ? 0 : 1)),
    onError: settle,
  });
  const clear = useMutation({
    mutationFn: (which: "all" | "read") => api.notifications.clear(which),
    onMutate: (which) => patch((items) => (which === "all" ? [] : items.filter((x) => !x.read_at)), (u) => (which === "all" ? 0 : u)),
    onSettled: settle,
  });

  const value = useMemo<NotificationsValue>(() => ({
    items: feed.data?.pages.flatMap((p) => p.items) ?? [],
    unread: feed.data?.pages[0]?.unread ?? 0,
    hasMore: !!feed.hasNextPage,
    loading: feed.isLoading,
    loadMore: () => feed.fetchNextPage(),
    setRead: (n, v) => read.mutate({ n, value: v }),
    readAll: () => readAll.mutate(),
    remove: (n) => remove.mutate(n),
    clear: (which) => clear.mutate(which),
    toasts,
    dismissToast,
  }), [feed, read, readAll, remove, clear, toasts, dismissToast]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
