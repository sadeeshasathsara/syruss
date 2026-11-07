// The organization's billing (/api/v1/billing): its plan, pages and credits, what is on offer, and buying.

export type OfferKind = "plan" | "addon" | "credits";

/** Something on offer, or what the organization has: a plan, an add-on (stacks by quantity) or a pack of prepaid pages. */
export interface Offer {
  id: string;
  kind: OfferKind;
  key: string;
  name: string;
  description: string | null;
  price_cents: number;
  currency: string;
  interval: "month" | "once";
  limits: Record<string, number | null>;
  pages: number | null;
}

export interface OrgLimit {
  key: string;
  label: string;
  noun: string;
  scope: "workspace" | "organization";
  metered: boolean;
  /** Null: unlimited. */
  value: number | null;
}

export interface OrgBilling {
  plan: Offer;
  /** The plan from the next period on (a downgrade waits for the period's end). */
  pending_plan: Offer | null;
  status: "trialing" | "active" | "canceled";
  period_start: string;
  period_end: string;
  trial_ends_at: string | null;
  limits: OrgLimit[];
  pages: { used: number; limit: number | null; by_workspace: { id: string; name: string; pages: number }[] };
  credits: { balance: number; history: { delta: number; kind: "purchase" | "grant" | "usage" | "refund" | "adjustment"; reason: string | null; created_at: string }[] };
  addons: { addon: Offer; quantity: number }[];
  payments: { amount_cents: number; currency: string; for: string | null; quantity: number; reference: string | null; created_at: string }[];
  requests: { id: string; item: Offer; quantity: number; status: "open" | "done" | "declined"; note: string | null; created_at: string; handled_at: string | null }[];
  provider: { kind: string; label: string; takes_payments: boolean };
}

export interface Offers {
  plan: Offer[];
  addon: Offer[];
  credits: Offer[];
}

export interface PurchaseStarted {
  status: "requested" | "redirect";
  message: string;
  url: string | null;
  request_id: string | null;
}
