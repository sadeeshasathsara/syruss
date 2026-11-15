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
    case "staff.invited":
      return `invited ${d.email ?? "someone"} to the staff as ${role(d.role)}`;
    case "staff.invitation_resent":
      return `sent the staff invitation to ${d.email ?? "someone"} again`;
    case "staff.invitation_revoked":
      return `withdrew the staff invitation to ${d.email ?? "someone"}`;
    case "staff.joined":
      return `joined the staff as ${role(d.role)}${d.customer_account_deleted ? " (their customer account was deleted)" : ""}`;
    case "staff.reviewed":
      return `confirmed ${who} still needs platform access`;
    case "staff.offboarded":
      return `took ${who} off the staff after a long time without using the admin app`;
    case "staff.policy_changed":
      return "changed when unused staff access is taken away and how often access is reviewed";
    case "approval.requested":
      return `asked a second owner to approve: ${d.summary ?? "a risky action"}`;
    case "approval.approved":
      return `approved ${d.requested_by ?? "someone"}'s request: ${d.summary ?? "a risky action"}`;
    case "approval.rejected":
      return `turned down ${d.requested_by ?? "someone"}'s request: ${d.summary ?? "a risky action"}`;
    case "approval.cancelled":
      return `withdrew their request: ${d.summary ?? "a risky action"}`;
    case "approval.failed":
      return `approved ${d.requested_by ?? "someone"}'s request, but it couldn't be carried out: ${d.summary ?? "a risky action"}`;
    case "approvals.on":
      return "turned on the second owner's approval for risky actions";
    case "approvals.off":
      return "turned off the second owner's approval";
    case "support.answered":
      return `answered support ticket ${d.number ?? ""}`.trim();
    case "support.noted":
      return `added a note to support ticket ${d.number ?? ""}`.trim();
    case "support.changed":
      return `changed support ticket ${d.number ?? ""}`.trim();
    case "support.merged":
      return `merged support ticket ${d.number ?? ""} into ${d.into ?? "another"}`;
    case "support.saved_reply_added":
    case "support.saved_reply_changed":
    case "support.saved_reply_removed":
      return `${a.action.endsWith("added") ? "added" : a.action.endsWith("removed") ? "removed" : "changed"} the saved reply "${d.title ?? ""}"`;
    case "support.targets_changed":
      return "changed the support service targets";
    case "lesson.added":
      return "added a platform lesson for the agent";
    case "lesson.active":
      return "approved a platform lesson for the agent";
    case "lesson.retired":
      return "retired a platform lesson";
    case "lesson.edited":
      return "edited a platform lesson";
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
    case "organization.suspended":
      return `suspended ${d.organization ?? "an organization"}`;
    case "organization.reactivated":
      return `reactivated ${d.organization ?? "an organization"}`;
    case "account.disabled":
      return `disabled ${who}'s account`;
    case "account.enabled":
      return `enabled ${who}'s account again`;
    case "account.browsers_forgotten":
      return `made ${who} confirm every browser again`;
    case "note.added":
      return "added a staff note";
    case "note.removed":
      return "removed a staff note";
    case "agent.paused":
      return `paused the agent for ${d.organization ?? "an organization"}`;
    case "agent.resumed":
      return `resumed the agent for ${d.organization ?? "an organization"}`;
    case "maintenance.on":
      return "put the platform in maintenance (read-only)";
    case "maintenance.off":
      return "ended maintenance";
    case "announcement.created":
      return `announced “${d.title ?? ""}” to ${d.audience ?? "everyone"}`;
    case "announcement.ended":
      return `ended the announcement “${d.title ?? ""}”`;
    default: {
      const flip = /^switch\.(signups|agent|intake)\.(on|off)$/.exec(a.action);
      if (flip) {
        const what = { signups: "new sign-ups", agent: "the agent for everyone", intake: "documents from other systems" }[flip[1]];
        return `${flip[2] === "on" ? "switched on" : "paused"} ${what}`;
      }
      return `${a.action.replace(/[._]/g, " ")} (${a.target_type} ${a.target_id})`;
    }
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
  { key: "organization,account,note", label: "Accounts" },
  { key: "maintenance,switch,announcement,agent", label: "Platform controls" },
] as const;
