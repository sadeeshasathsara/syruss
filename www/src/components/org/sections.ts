import { Building2, ShieldCheck, SlidersHorizontal, UserRound } from "lucide-react";
import type { ComponentType } from "react";

export interface OrgSettingsSection {
  key: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  subs: { key: string; label: string }[];
}

/**
 * Settings, organized by what they are about: the organization, you (profile), how you sign in (account and security),
 * and how the app behaves for you here (preferences).
 */
export const ORG_SETTINGS: OrgSettingsSection[] = [
  { key: "organization", label: "Organization", icon: Building2, subs: [
    { key: "general", label: "General" },
    { key: "danger", label: "Close the organization" },
  ] },
  { key: "profile", label: "Your profile", icon: UserRound, subs: [{ key: "details", label: "Details" }] },
  { key: "security", label: "Account and security", icon: ShieldCheck, subs: [
    { key: "sign-in", label: "Sign-in" },
    { key: "devices", label: "Trusted devices" },
    { key: "activity", label: "Security activity" },
  ] },
  { key: "preferences", label: "Preferences", icon: SlidersHorizontal, subs: [
    { key: "notifications", label: "Notifications" },
    { key: "appearance", label: "Appearance" },
  ] },
];

export const orgSettingsHref = (org: string, section: string, sub?: string) =>
  `/${org}/settings/${section}/${sub ?? ORG_SETTINGS.find((s) => s.key === section)?.subs[0].key ?? ""}`;
