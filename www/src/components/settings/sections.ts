import { Bell, Bot, FileCog, Plug, ScrollText, SlidersHorizontal, Users } from "lucide-react";
import type { ComponentType } from "react";
import type { Permission } from "@/lib/types";

export interface SettingsSection {
  key: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  subs: { key: string; label: string }[];
  /** A role without this permission doesn't see the section at all. */
  needs?: Permission;
}

/**
 * Workspace settings. In the workspace's navigation these are Settings' sub tabs; inside
 * settings they become the main tabs, each with its own sub tabs.
 */
export const SETTINGS: SettingsSection[] = [
  { key: "general", label: "General", icon: SlidersHorizontal, subs: [
    { key: "profile", label: "Profile" },
    { key: "billing", label: "Plan and billing" },
    { key: "danger", label: "Danger zone" },
  ] },
  { key: "members", label: "Members", icon: Users, needs: "members.view", subs: [
    { key: "people", label: "People" },
    { key: "invitations", label: "Invitations" },
    { key: "roles", label: "Roles and permissions" },
  ] },
  { key: "agent", label: "Agent", icon: Bot, subs: [
    { key: "autonomy", label: "Autonomy" },
    { key: "instructions", label: "Instructions" },
    { key: "budget", label: "Budget" },
    { key: "reflection", label: "Reflection" },
  ] },
  { key: "documents", label: "Documents", icon: FileCog, subs: [
    { key: "intake", label: "Intake" },
    { key: "review", label: "Review rules" },
  ] },
  { key: "notifications", label: "Notifications", icon: Bell, subs: [{ key: "preferences", label: "Your preferences" }] },
  { key: "integrations", label: "Integrations", icon: Plug, needs: "integrations.manage", subs: [
    { key: "api-keys", label: "API keys" },
    { key: "webhooks", label: "Webhooks" },
  ] },
  { key: "audit", label: "Audit log", icon: ScrollText, needs: "audit.view", subs: [
    { key: "activity", label: "Activity" },
    { key: "runs", label: "Agent runs" },
  ] },
];

export const settingsHref = (base: string, section: string, sub?: string) =>
  `${base}/settings/${section}/${sub ?? SETTINGS.find((s) => s.key === section)?.subs[0].key ?? ""}`;
