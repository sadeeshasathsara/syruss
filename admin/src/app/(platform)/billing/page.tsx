import { redirect } from "next/navigation";

const VIEWS = ["organizations", "invoices", "payments", "catalog", "settings"];

/** Billing opens on its first sub-tab; the old ?tab= addresses still lead to theirs (components/billing/BillingPage). */
export default async function BillingIndex({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab } = await searchParams;
  redirect(`/billing/${tab && VIEWS.includes(tab) ? tab : "organizations"}`);
}
