"use client";

import { useParams, useRouter } from "next/navigation";
import { useMemo } from "react";
import { ORG_SETTINGS, orgSettingsHref } from "@/components/org/sections";
import { PaletteProvider, type PaletteConfig, type Place } from "@/components/shell/CommandPalette";
import { useShell } from "@/components/shell/ShellContext";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { storeValue } from "@/lib/browser";

/** The organization's pages and every settings page, as places to go to. */
function places(org: string, canOrg: (p: string) => boolean): Place[] {
  return [
    { label: "Overview", href: `/${org}`, within: "Organization", keywords: "home dashboard" },
    { label: "All workspaces", href: `/${org}/workspaces`, within: "Organization" },
    ...(canOrg("workspaces.create") ? [{ label: "New workspace", href: `/${org}/new`, within: "Workspaces", keywords: "create add" }] : []),
    { label: "People", href: `/${org}/people`, within: "Organization", keywords: "members team invite" },
    ...(canOrg("org.audit.view") ? [{ label: "Activity", href: `/${org}/activity`, within: "Organization", keywords: "audit log history" }] : []),
    ...ORG_SETTINGS.flatMap((s) => s.subs.map((sub) => ({
      label: s.subs.length > 1 ? sub.label : s.label, href: orgSettingsHref(org, s.key, sub.key), within: s.subs.length > 1 ? `Settings · ${s.label}` : "Settings",
      keywords: `settings ${s.label}`,
    }))),
  ];
}

/**
 * The customer app's search, across the organization you are in: workspaces, documents you may view, people, your own
 * chats, your support requests and (for those who manage billing) invoices, plus the organization's pages.
 */
export function OrgSearch({ children }: { children: React.ReactNode }) {
  const { org } = useParams<{ org: string }>();
  const { me } = useMe();
  const { setChatOpen } = useShell();
  const router = useRouter();
  const perms = (me?.org_permissions ?? []).join();
  const config = useMemo<PaletteConfig>(() => ({
    search: api.search,
    scope: `org:${org}`,
    places: places(org, (p) => perms.split(",").includes(p)),
    placeholder: "Search workspaces, documents, people, chats…",
    hrefFor: (item) => `/${org}${item.href}`,
    // A chat opens in its workspace's chat panel
    open: (item, group) => {
      if (group !== "chats" || typeof item.workspace_id !== "string") return false;
      storeValue(`chat-session:${item.workspace_id}`, item.id);
      setChatOpen(true, "chat");
      router.push(`/${org}${item.href}`);
      return true;
    },
  }), [org, perms, setChatOpen, router]);
  return <PaletteProvider config={config}>{children}</PaletteProvider>;
}
