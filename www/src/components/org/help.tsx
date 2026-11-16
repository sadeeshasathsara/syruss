"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, FileText, Mail, MessageSquare, User } from "lucide-react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Section, SettingsHeader } from "@/components/settings/kit";
import { Bar, Empty } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { api, type SupportMessage, type TicketStatus } from "@/lib/api";
import { cn, dateTime } from "@/lib/format";
import { FileDrop } from "./FileDrop";

const STATUS: Record<TicketStatus, { label: string; tone: "info" | "warning" | "neutral" | "positive" }> = {
  new: { label: "Sent", tone: "info" },
  open: { label: "Being looked at", tone: "warning" },
  pending: { label: "Waiting for you", tone: "warning" },
  solved: { label: "Solved", tone: "positive" },
  closed: { label: "Closed", tone: "neutral" },
};
const CATEGORIES = [["billing", "Billing and payments"], ["account", "Account and sign-in"], ["technical", "Something isn't working"], ["other", "Something else"]];
const ACCEPT = ["application/pdf", "image/jpeg", "image/png", "image/webp"];

/** Your requests to the Syruss team: what's open, a new request, and each conversation (also carried on by email). */
export function HelpTickets() {
  const params = useSearchParams();
  const open = params.get("ticket");
  return open ? <TicketView id={open} /> : <TicketList />;
}

function TicketList() {
  const router = useRouter();
  const pathname = usePathname();
  const [show, setShow] = useState<"active" | "done">("active");
  const [writing, setWriting] = useState(false);
  const list = useQuery({ queryKey: ["support-tickets", show], queryFn: () => api.support.tickets(show) });
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Help & support" description="Ask the Syruss team for help. You can also ask the assistant in any workspace's chat: it answers what it can and opens a request for you when it can't."
        actions={!writing ? <Button variant="primary" onClick={() => setWriting(true)}>New request</Button> : undefined} />
      {writing && <NewTicket onDone={(id) => { setWriting(false); if (id) router.push(`${pathname}?ticket=${id}`); }} />}
      <Section title="Your requests" description={list.data?.can_see_all ? "As an owner or admin you see every request from this organization." : "The requests you made."}>
        <div className="mb-3 flex gap-1">
          {(["active", "done"] as const).map((s) => (
            <Button key={s} size="sm" variant={show === s ? "primary" : "secondary"} onClick={() => setShow(s)}>{s === "active" ? "Open" : "Solved"}</Button>
          ))}
        </div>
        {!list.data ? <Bar className="h-24 w-full" /> : !list.data.items.length ? (
          <Empty title={show === "active" ? "No open requests" : "Nothing solved yet"}>{show === "active" ? "When you ask for help, it shows here." : ""}</Empty>
        ) : (
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border">
            {list.data.items.map((t) => (
              <li key={t.id}>
                <button type="button" onClick={() => router.push(`${pathname}?ticket=${t.id}`)} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-raised">
                  <span className="num w-16 flex-none text-meta text-muted">{t.number}</span>
                  <span className="min-w-0 flex-1 truncate text-body text-ink">{t.subject}</span>
                  <Pill tone={STATUS[t.status].tone}>{STATUS[t.status].label}</Pill>
                  <span className="text-meta text-muted">{dateTime(t.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

function NewTicket({ onDone }: { onDone: (id?: string) => void }) {
  const queryClient = useQueryClient();
  const [subject, setSubject] = useState("");
  const [category, setCategory] = useState("billing");
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const send = useMutation({
    mutationFn: () => api.support.open({ subject: subject.trim(), body: body.trim(), category, files }),
    onSuccess: (t) => { queryClient.invalidateQueries({ queryKey: ["support-tickets"] }); onDone(t.id); },
  });
  return (
    <Section title="New request" description="Tell us what happened and what you expected. You'll get an email with its number, and the answer by email and here.">
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (subject.trim().length >= 3 && body.trim().length >= 5) send.mutate(); }}>
        <Field label="What's it about?">{(id) => (
          <select id={id} value={category} onChange={(e) => setCategory(e.target.value)} className="h-9 rounded-control border border-control bg-sheet px-2.5 text-body text-ink">
            {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        )}</Field>
        <Field label="Subject">{(id) => <Input id={id} value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} placeholder="My card payment was declined" />}</Field>
        <Field label="What happened?">{(id) => (
          <textarea id={id} value={body} onChange={(e) => setBody(e.target.value)} rows={6} maxLength={20000}
            className="w-full rounded-control border border-control bg-sheet px-3 py-2 text-body text-ink outline-none focus-visible:border-accent"
            placeholder="What you did, what you saw, and when. Screenshots help." />
        )}</Field>
        <FileDrop label="Screenshots or files (optional)" help="PDFs or pictures (JPEG, PNG or WebP), up to 10 MB each, up to 5." accept={ACCEPT}
          maxFiles={5} maxBytes={10 * 1024 * 1024} files={files} onChange={setFiles} />
        {send.error && <p className="text-body text-negative">{send.error.message}</p>}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={subject.trim().length < 3 || body.trim().length < 5 || send.isPending}>{send.isPending ? "Sending…" : "Send to the Syruss team"}</Button>
          <Button onClick={() => onDone()}>Cancel</Button>
        </div>
      </form>
    </Section>
  );
}

function TicketView({ id }: { id: string }) {
  const { org } = useParams<{ org: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const ticket = useQuery({ queryKey: ["support-ticket", id], queryFn: () => api.support.ticket(id), refetchInterval: 30_000 });
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const refresh = () => { queryClient.invalidateQueries({ queryKey: ["support-ticket", id] }); queryClient.invalidateQueries({ queryKey: ["support-tickets"] }); };
  const reply = useMutation({ mutationFn: () => api.support.reply(id, body.trim(), files), onSuccess: () => { setBody(""); setFiles([]); refresh(); } });
  const close = useMutation({ mutationFn: () => api.support.close(id), onSuccess: refresh });
  const t = ticket.data;
  if (ticket.isError) return <p className="py-6 text-body text-negative">{(ticket.error as Error).message}</p>;
  return (
    <div className="flex flex-col">
      <SettingsHeader title={t ? `${t.number} · ${t.subject}` : "Request"} description={t ? `${STATUS[t.status].label}. Replies come by email too; you can answer there or here.` : ""}
        actions={<Button onClick={() => router.push(pathname)}>All requests</Button>} />
      {!t ? <Bar className="h-48 w-full" /> : (
        <>
          <ol className="flex flex-col gap-3 py-4">
            {t.messages.map((m) => <Message key={m.id} m={m} ticketId={t.id} org={org} />)}
          </ol>
          {t.status === "closed" ? <p className="text-body text-muted">This request is closed. Start a new one if you need more help, and mention {t.number}.</p> : (
            <form className="flex flex-col gap-3 border-t border-border pt-4" onSubmit={(e) => { e.preventDefault(); if (body.trim()) reply.mutate(); }}>
              <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} maxLength={20000} aria-label="Your reply" placeholder="Write a reply"
                className="w-full rounded-control border border-control bg-sheet px-3 py-2 text-body text-ink outline-none focus-visible:border-accent" />
              <FileDrop label="Files (optional)" help="PDFs or pictures, up to 10 MB each, up to 5." accept={ACCEPT} maxFiles={5} maxBytes={10 * 1024 * 1024} files={files} onChange={setFiles} />
              {(reply.error ?? close.error) && <p className="text-body text-negative">{((reply.error ?? close.error) as Error).message}</p>}
              <div className="flex flex-wrap gap-2">
                <Button type="submit" variant="primary" disabled={!body.trim() || reply.isPending}>{reply.isPending ? "Sending…" : "Send reply"}</Button>
                {t.status !== "solved" && <Button disabled={close.isPending} onClick={() => close.mutate()}>This is solved</Button>}
              </div>
            </form>
          )}
        </>
      )}
    </div>
  );
}

function Message({ m, ticketId, org }: { m: SupportMessage; ticketId: string; org: string }) {
  if (m.author_kind === "system") return <li className="text-center text-meta text-muted">{m.body}</li>;
  const Icon = m.author_kind === "agent" ? Bot : m.author_kind === "staff" ? MessageSquare : m.via === "email" ? Mail : User;
  return (
    <li className={cn("rounded-card border px-4 py-3", m.author_kind === "staff" ? "border-accent/30 bg-[var(--pill-accent-bg)]" : "border-border bg-sheet")}>
      <p className="mb-1.5 flex flex-wrap items-center gap-2 text-meta text-muted">
        <Icon className="size-3.5" aria-hidden />
        <span className="font-medium text-ink">{m.author_kind === "staff" ? `${m.author_name}, Syruss support` : m.author_name}</span>
        <span>{dateTime(m.created_at)}</span>
      </p>
      <p className="whitespace-pre-wrap break-words text-body text-ink">{m.body}</p>
      {!!m.files.length && (
        <ul className="mt-2 flex flex-wrap gap-2">
          {m.files.map((f) => (
            <li key={f.id}>
              <a href={api.support.fileUrl(ticketId, f.id, org)} target="_blank" rel="noreferrer noopener"
                className="inline-flex items-center gap-1.5 rounded-control border border-border px-2 py-1 text-meta text-ink hover:bg-raised">
                <FileText className="size-3.5" aria-hidden />{f.filename}
              </a>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
