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
  /** The noun for exactly one: "API key". */
  one: string;
  scope: "workspace" | "organization";
  metered: boolean;
  /** Null: unlimited. */
  value: number | null;
}

/** A way to pay an invoice here (bank transfer with a slip, card). */
export interface PayMethod {
  key: string;
  label: string;
  customer_can_use: boolean;
  needs_proof: boolean;
  online: boolean;
}

export interface OrgBilling {
  plan: Offer;
  /** The plan from the next period on (a downgrade waits for the period's end). */
  pending_plan: Offer | null;
  status: "trialing" | "active" | "past_due" | "canceled";
  period_start: string;
  period_end: string;
  trial_ends_at: string | null;
  limits: OrgLimit[];
  pages: { used: number; limit: number | null; by_workspace: { id: string; name: string; pages: number }[] };
  credits: { balance: number; history: { delta: number; kind: "purchase" | "grant" | "usage" | "refund" | "adjustment"; reason: string | null; created_at: string }[] };
  addons: { addon: Offer; quantity: number }[];
  payments: { amount_cents: number; currency: string; method: string; invoice_id: string | null; invoice: string | null; for: string | null;
    quantity: number; reference: string | null; created_at: string }[];
  /** Issued invoices not paid yet, the soonest due first. */
  invoices_waiting: Invoice[];
  /** What the billing details still need before an invoice can be issued (empty: complete). */
  billing_details_missing: string[];
  methods: PayMethod[];
  /** A card kept to pay renewals by itself. */
  card: { kept: boolean; label: string | null; can_manage: boolean };
}

export interface Offers {
  plan: Offer[];
  addon: Offer[];
  credits: Offer[];
}

export interface PurchaseStarted {
  status: "invoiced" | "scheduled";
  message: string;
  invoice: Invoice | null;
}

/** Who invoices are made out to. */
export interface BillingDetails {
  legal_name: string;
  email: string;
  address_line1: string;
  address_line2: string | null;
  city: string;
  region: string | null;
  postal_code: string | null;
  country: string;
  tax_id: string | null;
  po_number: string | null;
}

export interface FileInfo {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  created_at: string;
}

export interface SentPayment {
  id: string;
  method: string;
  amount_cents: number;
  currency: string;
  paid_on: string;
  reference: string | null;
  note: string | null;
  status: "submitted" | "approved" | "rejected" | "withdrawn";
  submitted_by_name: string;
  rejection_reason: string | null;
  created_at: string;
  files: FileInfo[];
}

export interface BankAccount {
  bank: string;
  account_name: string;
  account_number: string;
  branch: string | null;
  swift: string | null;
  currency: string;
  instructions: string | null;
}

export interface Invoice {
  id: string;
  number: string;
  kind: "purchase" | "renewal" | "upgrade" | "manual";
  status: "open" | "paid" | "void" | "uncollectible";
  currency: string;
  subtotal_cents: number;
  tax_cents: number;
  total_cents: number;
  amount_paid_cents: number;
  amount_credited_cents: number;
  amount_refunded_cents: number;
  amount_due_cents: number;
  issued_at: string;
  due_at: string;
  paid_at: string | null;
  voided_at: string | null;
  period_start: string | null;
  period_end: string | null;
  overdue: boolean;
  tax_lines: { name: string; rate_bp: number; tax_cents: number; note: string | null }[];
  note: string | null;
  switched_on: boolean;
  has_pdf: boolean;
  has_receipt: boolean;
  lines?: { id: string; description: string; quantity: number; unit_cents: number; amount_cents: number; period_start: string | null; period_end: string | null }[];
  payments?: { id: string; method: string; amount_cents: number; currency: string; received_on: string | null; reference: string | null; created_at: string }[];
  how_to_pay?: (PayMethod & { instructions: { text?: string; reference?: string; accounts?: BankAccount[] } | null })[];
  submissions?: SentPayment[];
  credit_notes?: { id: string; number: string; amount_cents: number; currency: string; reason: string; created_at: string; has_pdf: boolean;
    refunds: { amount_cents: number; method: string; reference: string | null }[] }[];
}
