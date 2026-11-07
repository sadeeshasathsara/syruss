import type { StaffAction } from "./types";

/** What a staff action did, in words: "added Ana Perera to the staff as Support". */
export function describeAction(a: StaffAction): string {
  const d = a.details as Record<string, string | undefined>;
  const role = (r?: string) => (r ? r.charAt(0).toUpperCase() + r.slice(1) : "a role");
  const who = d.name || d.email || a.target_id;
  switch (a.action) {
    case "staff.added":
      return `added ${who} to the staff as ${role(d.role)}`;
    case "staff.role_changed":
      return `changed ${who}'s staff role from ${role(d.from)} to ${role(d.to)}`;
    case "staff.removed":
      return `removed ${who} from the staff (was ${role(d.role)})`;
    case "access.opened":
      return `opened ${d.organization ?? "an organization"}'s content for ${d.minutes ?? "a few"} minutes`;
    case "access.ended":
      return "closed their access to an organization's content";
    case "content.viewed":
      return `looked at a customer's ${a.target_type} (${a.target_id.slice(0, 8)})`;
    case "run.stopped":
      return `stopped an agent run (${a.target_id.slice(0, 8)})`;
    case "document.retried":
      return `sent a document to the agent again (${a.target_id.slice(0, 8)}, was ${String(d.from ?? "").toLowerCase()})`;
    case "event.resent":
      return `sent a waiting event again (${d.type ?? a.target_id.slice(0, 8)})`;
    case "billing.catalog_created":
      return `drafted ${d.key ?? "a catalog item"} version ${d.version ?? ""}`.trim();
    case "billing.catalog_changed":
      return `changed the draft ${a.target_id.slice(0, 8)}`;
    case "billing.catalog_published":
      return `put ${d.key ?? "a catalog item"} version ${d.version ?? ""} on sale`;
    case "billing.catalog_archived":
      return `took ${d.key ?? "a catalog item"} version ${d.version ?? ""} off sale`;
    case "billing.plan_moved":
      return `moved ${d.organizations ?? "the"} organizations to another plan version`;
    case "billing.plan_changed":
      return "changed an organization's plan";
    case "billing.addon_set":
      return `set an organization's add-on to ${d.quantity ?? "a new quantity"}`;
    case "billing.override_set":
      return `set a custom limit (${d.key ?? "a limit"}) for an organization`;
    case "billing.override_removed":
      return `removed a custom limit (${d.key ?? "a limit"}) for an organization`;
    case "billing.trial_started":
      return `started a ${d.days ?? ""}-day trial for an organization`;
    case "billing.credits_changed":
      return `changed an organization's prepaid credits by ${d.pages ?? "some"} pages`;
    case "billing.payment_recorded":
      return `recorded a payment of ${(Number(d.amount_cents ?? 0) / 100).toFixed(2)} ${d.currency ?? ""}`.trim();
    case "billing.request_fulfilled":
      return "completed a customer's billing request";
    case "billing.request_declined":
      return "declined a customer's billing request";
    default:
      return `${a.action.replace(/[._]/g, " ")} (${a.target_type} ${a.target_id})`;
  }
}

/** Filters for the audit log; each later phase adds its own group (organizations, accounts…). */
export const ACTION_GROUPS = [
  { key: "", label: "Everything" },
  { key: "staff", label: "Staff" },
  { key: "access", label: "Content access" },
  { key: "content", label: "Content viewed" },
  { key: "run,document,event", label: "Operations" },
  { key: "billing", label: "Billing" },
] as const;
