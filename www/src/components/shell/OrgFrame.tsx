"use client";

import { ArrowLeft, History, LayoutDashboard, LayoutGrid, Plus, Settings, UsersRound } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ORG_SETTINGS, orgSettingsHref } from "@/components/org/sections";
import { useMe } from "@/lib/access";
import { Brand } from "./Brand";
import { AppFrame } from "./AppFrame";
import { NavList, OrgSwitcher, WorkspaceSwitcher } from "./Nav";

/**
 * The frame for organization-level screens (no workspace selected, so no agent chat). The organization has five tabs:
 * Overview, Workspaces, People, Activity (for those who may audit) and Settings, which opens its own sections.
 */
export function OrgFrame({ org, title, children }: { org: string; title: string; children: React.ReactNode }) {
  const { canOrg } = useMe();
  const inSettings = usePathname().startsWith(`/${org}/settings`);
  const sidebar = inSettings ? (
    <>
      <Brand href={`/${org}`} />
      <Link
        href={`/${org}`}
        className="flex h-8 items-center gap-2 rounded-control px-2.5 text-body text-muted transition-colors hover:bg-active/60 hover:text-ink pointer-coarse:h-11"
      >
        <ArrowLeft className="size-4 flex-none" />
        <span className="min-w-0 truncate">Back to the organization</span>
      </Link>
      <p className="px-2.5 pt-1 text-meta font-medium text-muted">Settings</p>
      <NavList
        label="Settings"
        items={ORG_SETTINGS.map((s) => ({
          href: orgSettingsHref(org, s.key), label: s.label, icon: s.icon, also: [`/${org}/settings/${s.key}`],
          children: s.subs.map((sub) => ({ href: orgSettingsHref(org, s.key, sub.key), label: sub.label })),
        }))}
      />
    </>
  ) : (
    <>
      <Brand href={`/${org}`} />
      <OrgSwitcher org={org} />
      <WorkspaceSwitcher org={org} />
      <NavList
        label="Organization"
        items={[
          { href: `/${org}`, label: "Overview", icon: LayoutDashboard, exact: true },
          { href: `/${org}/workspaces`, label: "Workspaces", icon: LayoutGrid, children: [
            { href: `/${org}/workspaces`, label: "All workspaces" },
            ...(canOrg("workspaces.create") ? [{ href: `/${org}/new`, label: "New workspace" }] : []),
          ] },
          { href: `/${org}/people`, label: "People", icon: UsersRound },
          ...(canOrg("org.audit.view") ? [{ href: `/${org}/activity`, label: "Activity", icon: History }] : []),
          { href: orgSettingsHref(org, "organization"), label: "Settings", icon: Settings, also: [`/${org}/settings`] },
        ]}
      />
    </>
  );
  return (
    <AppFrame sidebar={sidebar} title={title} topbar={<span className="text-body text-muted">{title}</span>}>
      {children}
    </AppFrame>
  );
}
