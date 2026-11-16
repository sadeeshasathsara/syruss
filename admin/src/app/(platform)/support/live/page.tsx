"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, MessageSquare, User } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useStaff } from "@/components/shell/AdminFrame";
import { usePresence } from "@/components/support/Presence";
import { CustomerPanel, fillReply } from "@/components/support/Bits";
import { Bar, Empty, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { LiveChat, LiveChatItem, LiveMessage, Presence } from "@/lib/types";
import { Select } from "@/components/ui/Select";

const TYPING_EVERY_MS = 3000;
const TYPING_SHOWN_MS = 6000;

/** A short tone when a new chat starts waiting, so someone online notices without watching the page. */
function chime() {
  try {
    const audio = new AudioContext();
    const tone = audio.createOscillator();
    const gain = audio.createGain();
    tone.frequency.value = 880;
    gain.gain.setValueAtTime(0.08, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.35);
    tone.connect(gain).connect(audio.destination);
    tone.start();
    tone.stop(audio.currentTime + 0.35);
  } catch {
    // no sound available: the badge and the list still show it
  }
}

/** Live chats: customers who asked for a person in their chat. Accept one to talk with them there; Syruss's assistant
 * stays quiet until you end it. */
export default function LiveSupportPage() {
  const me = useStaff();
  const queryClient = useQueryClient();
  const { status, set } = usePresence();
  const [open, setOpen] = useState<string | null>(null);
  const queue = useQuery({ queryKey: ["support", "live", "queue"], queryFn: api.support.live.queue, refetchInterval: 15_000,
    enabled: me.can("support.respond") });
  const seen = useRef<Set<string> | null>(null);
  useLive({ scope: "support" }, (e) => {
    if (e.kind.startsWith("support.")) queryClient.invalidateQueries({ queryKey: ["support", "live", "queue"] });
  }, !me.can("support.respond"));
  // A chime for each chat that starts waiting while you're online (not for the ones already there when the page opened)
  useEffect(() => {
    const waiting = queue.data?.waiting.map((c) => c.conversation_id) ?? [];
    if (seen.current && status === "online" && waiting.some((id) => !seen.current!.has(id))) chime();
    if (queue.data) seen.current = new Set(waiting);
  }, [queue.data, status]);
  const mine = queue.data?.mine ?? [];
  const current = open && mine.some((c) => c.conversation_id === open) ? open : mine[0]?.conversation_id ?? null;
  const accept = useMutation({
    mutationFn: (id: string) => api.support.live.accept(id),
    onSuccess: (chat) => { setOpen(chat.conversation_id); queryClient.invalidateQueries({ queryKey: ["support"] }); },
    onError: () => queryClient.invalidateQueries({ queryKey: ["support", "live", "queue"] }),
  });

  if (!me.can("support.respond")) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title="Support" subtitle="Live chats with customers." />
        <p className="text-body text-muted">Live chats are for staff who answer support (the Support or Billing role).</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Support" subtitle="Customers who asked Syruss's assistant for a person. Accept one to talk with them in their own chat."
        actions={<PresencePicker status={status} onChange={set} />} />
      {status !== "online" && (
        <p className="rounded-control bg-[var(--pill-warning-bg)] px-3 py-2 text-body text-[var(--pill-warning-ink)]">
          You&apos;re {status}. While nobody is online, the assistant offers customers a ticket instead of a live chat.
        </p>
      )}
      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          <Panel title="Waiting" aside={<span className="text-meta text-muted">{queue.data?.online.length ?? 0} online · {queue.data?.others_live ?? 0} with others</span>}>
            {!queue.data ? <Bar className="h-24 w-full" /> : !queue.data.waiting.length ? <p className="text-meta text-muted">Nobody is waiting.</p> : (
              <ul className="flex flex-col gap-3">
                {queue.data.waiting.map((c) => <Waiting key={c.conversation_id} c={c} busy={accept.isPending} onAccept={() => accept.mutate(c.conversation_id)} />)}
              </ul>
            )}
            <Problem error={accept.error} />
          </Panel>
          <Panel title="Your chats">
            {!mine.length ? <p className="text-meta text-muted">Chats you accept show here.</p> : (
              <ul className="flex flex-col gap-1">
                {mine.map((c) => (
                  <li key={c.conversation_id}>
                    <button type="button" onClick={() => setOpen(c.conversation_id)}
                      className={cn("w-full rounded-control px-2.5 py-2 text-left", c.conversation_id === current ? "bg-active" : "hover:bg-raised")}>
                      <span className="block truncate text-body font-medium text-ink">{c.person ?? "Customer"}</span>
                      <span className="block truncate text-meta text-muted">{c.organization} · {c.reason}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        {current ? <ChatPane key={current} id={current} online={queue.data?.online ?? []} /> : (
          <Empty title="No chat open">Accept a waiting chat to start. The customer sees your first name and is told you can see the conversation.</Empty>
        )}
      </div>
    </div>
  );
}

function PresencePicker({ status, onChange }: { status: Presence; onChange: (s: Presence) => Promise<void> }) {
  const [saving, setSaving] = useState(false);
  return (
    <label className="flex items-center gap-2 text-meta text-muted">
      <span className={cn("size-2 rounded-full", status === "online" ? "bg-[var(--pill-positive-ink)]" : status === "away" ? "bg-[var(--pill-warning-ink)]" : "bg-faint")} aria-hidden />
      <Select aria-label="Your live chat status" value={status} disabled={saving}
        onValueChange={async (v) => { setSaving(true); try { await onChange(v as Presence); } finally { setSaving(false); } }}>
        <option value="online">Online: take chats</option>
        <option value="away">Away</option>
        <option value="offline">Offline</option>
      </Select>
    </label>
  );
}

function Waiting({ c, busy, onAccept }: { c: LiveChatItem; busy: boolean; onAccept: () => void }) {
  return (
    <li className="flex flex-col gap-1.5 rounded-card border border-border p-3">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{c.person ?? "Customer"}</span>
        {c.requested_at && <span className="text-meta text-muted">{ago(c.requested_at)}</span>}
      </div>
      <p className="truncate text-meta text-muted">{c.organization ?? "Unknown organization"}{c.workspace && ` · ${c.workspace}`}</p>
      {c.reason && <p className="text-body text-ink">{c.reason}</p>}
      {c.summary && <p className="line-clamp-3 text-meta text-muted">{c.summary}</p>}
      <Button size="sm" variant="primary" disabled={busy} onClick={onAccept}>Accept</Button>
    </li>
  );
}

function ChatPane({ id, online }: { id: string; online: { id: string; name: string }[] }) {
  const me = useStaff();
  const queryClient = useQueryClient();
  const key = useMemo(() => ["support", "live", "chat", id], [id]);
  const chat = useQuery({ queryKey: key, queryFn: () => api.support.live.chat(id) });
  const [typing, setTyping] = useState<string | null>(null);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const live = chat.data?.support?.state === "live";
  useLive({ scope: "chat", id }, (e) => {
    const data = e as unknown as LiveMessage & { kind: string; name?: string };
    if (e.kind === "support.message") {
      queryClient.setQueryData<LiveChat>(key, (now) => now && !now.messages.some((m) => m.id === data.id)
        ? { ...now, messages: [...now.messages, { id: data.id, role: data.role, content: data.content, staff_name: data.staff_name, at: data.at }] } : now);
      if (data.role === "user") setTyping(null);
    } else if (e.kind === "support.typing") {
      setTyping(data.name ?? "They");
      clearTimeout(typingTimer.current);
      typingTimer.current = setTimeout(() => setTyping(null), TYPING_SHOWN_MS);
    } else if (e.kind === "support.state") {
      queryClient.invalidateQueries({ queryKey: ["support", "live"] });
    }
  }, !live);
  useEffect(() => () => clearTimeout(typingTimer.current), []);
  const bottom = useRef<HTMLLIElement>(null);
  // Braces: scrollIntoView returns a Promise in newer browsers, and an effect may only return its clean-up
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [chat.data?.messages.length, typing]);

  if (chat.isError) return <Problem error={chat.error} />;
  const c = chat.data;
  if (!c) return <Bar className="h-96 w-full" />;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
      <div className="flex min-h-[28rem] flex-col rounded-card border border-border bg-sheet">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
          <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{c.person ?? "Customer"} · {c.title}</span>
          {live ? <Pill tone="positive">Live</Pill> : <Pill tone="neutral">Ended</Pill>}
          {c.support?.ticket_number && <Pill tone="info">{c.support.ticket_number}</Pill>}
        </div>
        <ol className="flex max-h-[60vh] min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-3">
          {c.messages.map((m) => <Line key={m.id} m={m} person={c.person ?? "Customer"} mine={m.role === "staff" && m.staff_name === me.name} />)}
          {typing && <li className="text-meta italic text-muted">{typing} is typing…</li>}
          <li ref={bottom} aria-hidden />
        </ol>
        {live ? <Composer chat={c} chatKey={key} /> : <p className="border-t border-border px-4 py-3 text-meta text-muted">This chat has ended. Syruss&apos;s assistant answers the customer again.</p>}
      </div>
      <div className="flex flex-col gap-4">
        <Panel title="Handover from the assistant">
          <p className="text-body font-medium text-ink">{c.reason}</p>
          {c.summary && <p className="mt-1 whitespace-pre-wrap text-meta text-muted">{c.summary}</p>}
        </Panel>
        {live && <Actions chat={c} online={online.filter((p) => p.id !== me.user_id)} />}
        <CustomerPanel c={c.context} missing="No Syruss account found." />
      </div>
    </div>
  );
}

function Line({ m, person, mine }: { m: LiveMessage; person: string; mine: boolean }) {
  if (m.role === "system") return <li className="text-center text-meta text-muted">{m.content}</li>;
  const who = m.role === "user" ? person : m.role === "assistant" ? "Syruss assistant" : `${m.staff_name} (Syruss team)`;
  const Icon = m.role === "user" ? User : m.role === "assistant" ? Bot : MessageSquare;
  return (
    <li className={cn("max-w-[85%] rounded-card px-3 py-2", m.role === "staff" ? "self-end bg-[var(--pill-accent-bg)]" : "self-start bg-raised")}>
      <p className="mb-0.5 flex items-center gap-1.5 text-meta text-muted">
        <Icon className="size-3.5" aria-hidden /><span className="font-medium text-ink">{mine ? "You" : who}</span><span>{dateTime(m.at)}</span>
      </p>
      <p className="whitespace-pre-wrap break-words text-body text-ink">{m.content}</p>
    </li>
  );
}

function Composer({ chat, chatKey }: { chat: LiveChat; chatKey: readonly unknown[] }) {
  const me = useStaff();
  const queryClient = useQueryClient();
  const [body, setBody] = useState("");
  const lastTyping = useRef(0);
  const saved = useQuery({ queryKey: ["support", "saved"], queryFn: api.support.savedReplies });
  const values = { first_name: (chat.person ?? "").split(" ")[0] || "there", ticket: chat.support?.ticket_number ?? "", name: me.name };
  const slash = body.match(/(?:^|\s)\/([a-z0-9-]*)$/);
  const matches = slash ? (saved.data ?? []).filter((r) => r.shortcut?.startsWith(slash[1]) || r.title.toLowerCase().includes(slash[1])) : [];
  const insert = (text: string) => setBody((now) => (slash ? now.slice(0, now.length - slash[0].trimStart().length) : now) + fillReply(text, values));
  const send = useMutation({
    mutationFn: () => api.support.live.say(chat.conversation_id, body.trim()),
    onSuccess: (m) => {
      setBody("");
      queryClient.setQueryData<LiveChat>(chatKey, (now) => now && !now.messages.some((x) => x.id === m.id) ? { ...now, messages: [...now.messages, m] } : now);
    },
  });
  const typed = (text: string) => {
    setBody(text);
    if (text.trim() && Date.now() - lastTyping.current > TYPING_EVERY_MS) {
      lastTyping.current = Date.now();
      api.support.live.typing(chat.conversation_id).catch(() => undefined);
    }
  };
  return (
    <form className="flex flex-col gap-2 border-t border-border p-3" onSubmit={(e) => { e.preventDefault(); if (body.trim()) send.mutate(); }}>
      <div className="relative">
        <textarea value={body} onChange={(e) => typed(e.target.value)} rows={3} maxLength={5000} aria-label="Your message"
          placeholder={`Write to ${values.first_name}. Enter sends, Shift+Enter for a new line, / for a saved reply.`}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !matches.length) { e.preventDefault(); if (body.trim()) send.mutate(); } }}
          className="w-full resize-y rounded-control border border-control bg-sheet px-3 py-2 text-body text-ink outline-none focus-visible:border-accent" />
        {!!matches.length && (
          <ul className="absolute bottom-full left-0 z-10 mb-1 max-h-56 w-72 overflow-y-auto rounded-card border border-border bg-sheet py-1 [box-shadow:var(--shadow-card)]">
            {matches.map((r) => (
              <li key={r.id}><button type="button" onClick={() => insert(r.body)} className="w-full px-3 py-1.5 text-left text-body hover:bg-raised">
                {r.title} {r.shortcut && <span className="text-meta text-muted">/{r.shortcut}</span>}
              </button></li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex items-center gap-2">
        <span className="flex-1 text-meta text-muted">The customer sees your first name.</span>
        <Button type="submit" size="sm" variant="primary" disabled={!body.trim() || send.isPending}>{send.isPending ? "Sending…" : "Send"}</Button>
      </div>
      <Problem error={send.error} />
    </form>
  );
}

function Actions({ chat, online }: { chat: LiveChat; online: { id: string; name: string }[] }) {
  const queryClient = useQueryClient();
  const [to, setTo] = useState("");
  const done = () => queryClient.invalidateQueries({ queryKey: ["support"] });
  const transfer = useMutation({ mutationFn: () => api.support.live.transfer(chat.conversation_id, to), onSuccess: done });
  const ticket = useMutation({ mutationFn: () => api.support.live.ticket(chat.conversation_id), onSuccess: done });
  const end = useMutation({ mutationFn: () => api.support.live.end(chat.conversation_id), onSuccess: done });
  return (
    <Panel title="This chat">
      <div className="flex flex-col gap-2.5">
        {!chat.support?.ticket_number && (
          <Button size="sm" disabled={ticket.isPending} onClick={() => ticket.mutate()}>{ticket.isPending ? "Making a ticket…" : "Make a ticket to follow up"}</Button>
        )}
        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (to) transfer.mutate(); }}>
          <Select aria-label="Hand this chat to" value={to} onValueChange={(v) => setTo(v)} className="min-w-0 flex-1">
            <option value="">{online.length ? "Hand to…" : "Nobody else is online"}</option>
            {online.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
          <Button type="submit" size="sm" disabled={!to || transfer.isPending}>Hand over</Button>
        </form>
        <Button size="sm" variant="danger" disabled={end.isPending} onClick={() => end.mutate()}>End chat</Button>
        <p className="text-meta text-muted">Ending hands the chat back to Syruss&apos;s assistant.</p>
        <Problem error={transfer.error ?? ticket.error ?? end.error} />
      </div>
    </Panel>
  );
}
