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
    default:
      return `${a.action.replace(/[._]/g, " ")} (${a.target_type} ${a.target_id})`;
  }
}

/** Filters for the audit log; each later phase adds its own group (organizations, accounts…). */
export const ACTION_GROUPS = [
  { key: "", label: "Everything" },
  { key: "staff", label: "Staff" },
] as const;
