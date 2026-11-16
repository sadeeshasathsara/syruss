"use client";

import { useMemo } from "react";
import { PaletteProvider, type PaletteConfig, type Place } from "@/components/shell/CommandPalette";
import { api } from "@/lib/api";
import { NAV } from "@/lib/nav";

/** Every page in the navigation you may open, as places to go to. */
function places(permissions: string[]): Place[] {
  const can = (p?: string) => !p || permissions.includes(p);
  const out: Place[] = [];
  for (const group of NAV) {
    for (const section of group.sections.filter((s) => can(s.permission))) {
      const children = (section.children ?? []).filter((c) => can(c.permission));
      if (!children.length) out.push({ label: section.label, href: section.href, within: group.label });
      for (const c of children) out.push({ label: c.label, href: c.href, within: section.label, keywords: group.label });
    }
  }
  return out;
}

/**
 * The admin app's search: organizations, people, documents, agent runs, support tickets, invoices, workspaces and staff
 * across the platform, plus every page you can open.
 */
export function AdminSearch({ permissions, children }: { permissions: string[]; children: React.ReactNode }) {
  const signature = permissions.join();
  const config = useMemo<PaletteConfig>(() => ({
    search: api.console.search,
    scope: "admin",
    places: places(signature.split(",")),
    placeholder: "Search organizations, people, documents, tickets, ids…",
  }), [signature]);
  return <PaletteProvider config={config}>{children}</PaletteProvider>;
}
