import type { Tone } from "@/components/ui/Pill";
import type { CatalogItem, CatalogKind, Invoice, InvoiceStatus, LimitInfo, Limits, Submission } from "./types";

/** "$49.00", "LKR 4,900.00": money in its own currency. */
export function money(cents: number, currency = "USD"): string {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

/** A limit's value in words: "Unlimited", "2,000". */
export const amount = (v: number | null | undefined) => (v == null ? "Unlimited" : v.toLocaleString());

export const KIND_LABEL: Record<CatalogKind, { one: string; many: string }> = {
  plan: { one: "Plan", many: "Plans" },
  addon: { one: "Add-on", many: "Add-ons" },
  credits: { one: "Credit pack", many: "Credit packs" },
};

export const ITEM_STATUS: Record<CatalogItem["status"], { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "warning" },
  published: { label: "On sale", tone: "positive" },
  archived: { label: "Archived", tone: "neutral" },
};

/** What a catalog item is, in one line: "2,000 pages a month, 5 channels", "1,000 pages", "+1,000 pages a month". */
export function itemSummary(item: Pick<CatalogItem, "kind" | "limits" | "pages">, limits: Record<string, LimitInfo>): string {
  if (item.kind === "credits") return `${(item.pages ?? 0).toLocaleString()} prepaid pages`;
  const sign = item.kind === "addon" ? "+" : "";
  const parts = Object.entries(item.limits as Limits).map(([k, v]) => `${sign}${amount(v)} ${limits[k] ? (v === 1 ? limits[k].one : limits[k].noun) : k}`);
  return parts.join(" · ") || (item.kind === "plan" ? "Every limit unlimited" : "Adds nothing yet");
}

export const price = (item: Pick<CatalogItem, "price_cents" | "currency" | "interval">) =>
  item.price_cents ? `${money(item.price_cents, item.currency)}${item.interval === "month" ? " a month" : ""}` : "No charge";

const INVOICE_STATUS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  open: { label: "Waiting for payment", tone: "warning" },
  paid: { label: "Paid", tone: "positive" },
  void: { label: "Void", tone: "neutral" },
  uncollectible: { label: "Not paid", tone: "negative" },
};

/** An invoice's state in a word, with overdue ones called so. */
export function invoiceStatus(i: Pick<Invoice, "status" | "overdue">): { label: string; tone: Tone } {
  return i.status === "open" && i.overdue ? { label: "Overdue", tone: "negative" } : INVOICE_STATUS[i.status];
}

export const SUBMISSION_STATUS: Record<Submission["status"], { label: string; tone: Tone }> = {
  submitted: { label: "To check", tone: "warning" },
  approved: { label: "Approved", tone: "positive" },
  rejected: { label: "Rejected", tone: "negative" },
  withdrawn: { label: "Withdrawn", tone: "neutral" },
};

export const KIND_OF_INVOICE: Record<Invoice["kind"], string> = { purchase: "Purchase", renewal: "Renewal", upgrade: "Upgrade", manual: "Custom" };

export const METHOD_LABEL: Record<string, string> = { bank_transfer: "Bank transfer", staff_recorded: "Cash or cheque", card: "Card" };

export const day = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
