"use client";

import * as D from "@radix-ui/react-dialog";
import { Bell, BellOff, Check, CheckCheck, CircleAlert, CircleCheck, Info, Mail, Trash2, TriangleAlert, Volume2, VolumeX, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, IconButton } from "@/components/ui/Button";
import { ago, cn } from "@/lib/format";
import { useNotifications } from "@/lib/notifications";
import { setSound, useSoundOn } from "@/lib/sound";
import type { AppNotification } from "@/lib/types";

const DOT: Record<AppNotification["severity"], string> = {
  info: "bg-accent",
  success: "bg-positive",
  warning: "bg-[var(--pill-warning-ink)]",
  error: "bg-negative",
};

/** A path inside the app, or a full address (an invitation opens on the site). */
function follow(router: ReturnType<typeof useRouter>, link: string) {
  if (/^https?:\/\//.test(link)) window.location.assign(link);
  else router.push(link);
}

function Item({ n, onOpen }: { n: AppNotification; onOpen: (n: AppNotification) => void }) {
  const { setRead, remove } = useNotifications();
  const unread = !n.read_at;
  return (
    <li className={cn("group relative flex animate-rise gap-3 border-b border-rule px-4 py-3 last:border-0 transition-colors hover:bg-raised", unread && "bg-accent-soft/50")}>
      <span aria-hidden className={cn("mt-1.5 size-2 flex-none rounded-full", unread ? DOT[n.severity] : "bg-transparent")} />
      <button type="button" onClick={() => onOpen(n)} className="min-w-0 flex-1 text-left">
        <span className={cn("block text-body text-ink", unread && "font-medium")}>{n.title}</span>
        {n.body && <span className="mt-0.5 line-clamp-2 block text-body text-muted">{n.body}</span>}
        <span className="mt-1 block text-meta text-muted">{ago(n.created_at)}</span>
      </button>
      <span className="flex flex-none items-start gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
        <IconButton aria-label={unread ? "Mark as read" : "Mark as unread"} onClick={() => setRead(n, unread)}>
          {unread ? <Check /> : <Mail />}
        </IconButton>
        <IconButton aria-label="Delete" onClick={() => remove(n)}>
          <Trash2 />
        </IconButton>
      </span>
    </li>
  );
}

/** The bell: your notifications from every workspace, live. */
/** Whether new notifications make a sound on this device. */
function SoundSwitch() {
  const on = useSoundOn();
  return (
    <IconButton
      aria-label={on ? "Turn the notification sound off" : "Turn the notification sound on"}
      aria-pressed={on}
      onClick={() => setSound(!on)}
    >
      {on ? <Volume2 /> : <VolumeX />}
    </IconButton>
  );
}

export function NotificationBell() {
  const router = useRouter();
  const { items, unread, hasMore, loading, loadMore, readAll, clear, setRead } = useNotifications();
  const [open, setOpen] = useState(false);
  const [onlyUnread, setOnlyUnread] = useState(false);
  const shown = onlyUnread ? items.filter((n) => !n.read_at) : items;

  const openItem = (n: AppNotification) => {
    if (!n.read_at) setRead(n, true);
    setOpen(false);
    if (n.link) follow(router, n.link);
  };

  return (
    <D.Root open={open} onOpenChange={setOpen} modal={false}>
      <D.Trigger asChild>
        <button
          type="button"
          aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
          className="relative flex size-8 items-center justify-center rounded-control text-muted transition-colors hover:bg-active/60 hover:text-ink pointer-coarse:size-10"
        >
          <Bell className="size-4" />
          {unread > 0 && (
            <span className="num absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-negative px-1 text-[11px] font-semibold leading-none text-white">
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </button>
      </D.Trigger>
      <D.Portal>
        <D.Content
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className="fixed right-2 top-12 z-50 flex max-h-[min(640px,calc(100dvh-64px))] w-[min(420px,calc(100vw-16px))] animate-rise flex-col overflow-hidden rounded-card border border-border bg-sheet shadow-float focus:outline-none"
        >
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <D.Title className="flex-1 text-section font-semibold text-ink">
              Notifications {unread > 0 && <span className="num ml-1 text-body font-normal text-muted">{unread} unread</span>}
            </D.Title>
            <D.Close asChild>
              <IconButton aria-label="Close"><X /></IconButton>
            </D.Close>
          </div>
          <div className="flex flex-wrap items-center gap-1 border-b border-border px-3 py-2">
            {(["All", "Unread"] as const).map((label) => {
              const active = (label === "Unread") === onlyUnread;
              return (
                <button
                  key={label}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setOnlyUnread(label === "Unread")}
                  className={cn("h-7 rounded-control px-2.5 text-body transition-colors", active ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}
                >
                  {label}
                </button>
              );
            })}
            <span className="flex-1" />
            <SoundSwitch />
            <Button size="sm" variant="ghost" disabled={unread === 0} onClick={readAll}><CheckCheck /> Mark all read</Button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
            {loading ? (
              <p className="px-4 py-6 text-body text-muted">Loading…</p>
            ) : shown.length === 0 ? (
              <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
                <BellOff className="size-5 text-faint" />
                <p className="text-body font-medium text-ink">{onlyUnread ? "Nothing unread" : "You're all caught up"}</p>
                <p className="text-body text-muted">Reviews, questions from the agent and other things that need you show up here.</p>
              </div>
            ) : (
              <ul>
                {shown.map((n) => <Item key={n.id} n={n} onOpen={openItem} />)}
              </ul>
            )}
            {hasMore && !onlyUnread && (
              <div className="px-4 py-3">
                <Button size="sm" variant="secondary" onClick={loadMore}>Load older</Button>
              </div>
            )}
          </div>
          {items.length > 0 && (
            <div className="flex items-center justify-end gap-1 border-t border-border px-3 py-2">
              <Button size="sm" variant="ghost" onClick={() => clear("read")}>Clear read</Button>
              <Button size="sm" variant="ghost" onClick={() => clear("all")}>Clear all</Button>
            </div>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

const TOAST_MS = 6000;
const TOAST_ICON = { info: Info, success: CircleCheck, warning: TriangleAlert, error: CircleAlert } as const;
const TOAST_TONE: Record<AppNotification["severity"], string> = {
  info: "text-accent", success: "text-positive", warning: "text-[var(--pill-warning-ink)]", error: "text-negative",
};

function Toast({ n }: { n: AppNotification }) {
  const router = useRouter();
  const { dismissToast, setRead } = useNotifications();
  const [paused, setPaused] = useState(false);
  const Icon = TOAST_ICON[n.severity] ?? Info;

  // It leaves by itself after a few seconds, but waits while the pointer is on it.
  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(() => dismissToast(n.id), TOAST_MS);
    return () => clearTimeout(timer);
  }, [paused, n.id, dismissToast]);

  const open = () => {
    setRead(n, true);
    dismissToast(n.id);
    if (n.link) follow(router, n.link);
  };
  return (
    <div
      role="status"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className="syruss-toast pointer-events-auto relative flex items-center gap-2.5 overflow-hidden rounded-card border border-border bg-sheet py-2 pl-3 pr-1.5 shadow-float"
    >
      <Icon aria-hidden className={cn("size-4 flex-none", TOAST_TONE[n.severity] ?? TOAST_TONE.info)} />
      <button type="button" onClick={open} className="min-w-0 flex-1 text-left">
        <span className="block truncate text-body font-medium text-ink">{n.title}</span>
        {n.body && <span className="block truncate text-meta text-muted">{n.body}</span>}
      </button>
      <IconButton aria-label="Dismiss" onClick={() => dismissToast(n.id)}><X /></IconButton>
      <span aria-hidden className="syruss-toast-timer" style={{ animationPlayState: paused ? "paused" : "running", animationDuration: `${TOAST_MS}ms` }} />
    </div>
  );
}

/** New notifications, briefly, at the top right: small, one line each, newest first. */
export function NotificationToasts() {
  const { toasts } = useNotifications();
  return (
    <div aria-live="polite" className="pointer-events-none fixed right-3 top-14 z-50 flex w-[min(320px,calc(100vw-24px))] flex-col gap-2">
      {toasts.map((n) => <Toast key={n.id} n={n} />)}
    </div>
  );
}
