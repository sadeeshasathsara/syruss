"use client";

import { usePathname } from "next/navigation";
import { Tabs } from "@/components/ui/Blocks";
import { Pill, type Tone } from "@/components/ui/Pill";
import type { TicketPriority, TicketStatus } from "@/lib/types";

export const STATUS: Record<TicketStatus, { label: string; tone: Tone }> = {
  new: { label: "New", tone: "info" },
  open: { label: "Open", tone: "warning" },
  pending: { label: "Waiting for customer", tone: "neutral" },
  solved: { label: "Solved", tone: "positive" },
  closed: { label: "Closed", tone: "neutral" },
};

export const PRIORITY: Record<TicketPriority, { label: string; tone: Tone }> = {
  urgent: { label: "Urgent", tone: "negative" },
  high: { label: "High", tone: "warning" },
  normal: { label: "Normal", tone: "neutral" },
  low: { label: "Low", tone: "neutral" },
};

export const CATEGORY: Record<string, string> = { billing: "Billing", account: "Account", technical: "Technical", other: "Other" };

export function StatusPill({ status }: { status: TicketStatus }) {
  return <Pill tone={STATUS[status].tone}>{STATUS[status].label}</Pill>;
}

export function PriorityPill({ priority }: { priority: TicketPriority }) {
  return priority === "normal" ? null : <Pill tone={PRIORITY[priority].tone}>{PRIORITY[priority].label}</Pill>;
}

/** The Support section's views. */
export function SupportTabs({ counts }: { counts?: { active?: number } }) {
  const path = usePathname();
  return (
    <Tabs items={[
      { href: "/support", label: "Tickets", count: counts?.active, active: path === "/support" || path.startsWith("/support/tickets") },
      { href: "/support/saved-replies", label: "Saved replies", active: path.startsWith("/support/saved-replies") },
      { href: "/support/settings", label: "Settings", active: path.startsWith("/support/settings") },
    ]} />
  );
}

/** A due time as people read it: "due in 3h", "4h late". */
export function dueIn(iso: string | null, now = Date.now()): { text: string; late: boolean } | null {
  if (!iso) return null;
  const minutes = Math.round((new Date(iso).getTime() - now) / 60000);
  const span = (m: number) => (m < 60 ? `${m}m` : m < 48 * 60 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`);
  return minutes >= 0 ? { text: `due in ${span(minutes)}`, late: false } : { text: `${span(-minutes)} late`, late: true };
}

/** Saved reply text with its placeholders filled for this ticket. */
export function fillReply(body: string, values: { first_name: string; ticket: string; name: string }): string {
  return body.replace(/\{\{\s*(first_name|ticket|name)\s*\}\}/g, (_, key: keyof typeof values) => values[key]);
}
