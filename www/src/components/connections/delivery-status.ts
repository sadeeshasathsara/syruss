import type { Tone } from "@/lib/format";
import type { DeliveryStatus } from "@/lib/types";

export const DELIVERY_STATUS: Record<DeliveryStatus, { label: string; tone: Tone; help: string }> = {
  held: { label: "Held back", tone: "warning", help: "Waiting until nothing is in doubt about the document." },
  pending: { label: "Waiting to send", tone: "info", help: "Queued and about to be sent." },
  sending: { label: "Sending", tone: "info", help: "Being sent right now." },
  retrying: { label: "Will retry", tone: "warning", help: "The last try failed; it will be tried again." },
  delivered: { label: "Delivered", tone: "positive", help: "The other system accepted it." },
  dead: { label: "Failed", tone: "negative", help: "Every try failed. Fix the problem, then replay it." },
  cancelled: { label: "Cancelled", tone: "neutral", help: "Not sent." },
};

/** The filters people use, each a group of statuses. */
export const DELIVERY_FILTERS: { key: string; label: string; statuses: DeliveryStatus[] }[] = [
  { key: "all", label: "All", statuses: [] },
  { key: "held", label: "Held back", statuses: ["held"] },
  { key: "waiting", label: "Waiting", statuses: ["pending", "sending", "retrying"] },
  { key: "delivered", label: "Delivered", statuses: ["delivered"] },
  { key: "failed", label: "Failed", statuses: ["dead"] },
  { key: "cancelled", label: "Cancelled", statuses: ["cancelled"] },
];

/** "in 4 min" for a time ahead, "now" when it has come. */
export function inTime(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const min = Math.round((new Date(iso).getTime() - now) / 60000);
  if (min <= 0) return "now";
  if (min < 60) return `in ${min} min`;
  const h = Math.round(min / 60);
  return h < 24 ? `in ${h} h` : `in ${Math.round(h / 24)} d`;
}

export const KIND_NOUN: Record<string, string> = {
  webhook: "Webhook", rest: "REST API", s3: "Storage bucket", email: "Email", quickbooks: "QuickBooks Online", xero: "Xero",
  netsuite: "NetSuite", sap: "SAP S/4HANA", dynamics: "Dynamics 365 Business Central", odoo: "Odoo",
};
