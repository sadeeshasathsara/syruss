import { Bot, Building2, CreditCard, Hourglass, LayoutDashboard, LifeBuoy, LockKeyhole, Mail, ScrollText, SlidersHorizontal, UsersRound } from "lucide-react";
import type { ComponentType } from "react";

/** What a count on the navigation shows: work waiting for someone. */
export type CountKey = "tickets" | "liveChats" | "payments" | "approvals";

export interface NavChild {
  href: string;
  label: string;
  count?: CountKey;
  /** Paths below which this sub-tab stays lit (detail pages: an invoice under Invoices). */
  also?: string[];
  permission?: string;
}

export interface NavSection {
  href: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  count?: CountKey;
  /** Other paths that make this section the one you are in. */
  also?: string[];
  children?: NavChild[];
  /** Hidden from staff whose role lacks it (the server checks every call regardless). */
  permission?: string;
}

export interface NavGroup {
  label: string;
  sections: NavSection[];
}

/**
 * The admin app's navigation, in the order staff use it: watch the platform, help customers, run Syruss. A section with
 * sub-tabs opens to show them while you are in it; every sub-tab is its own address.
 */
export const NAV: NavGroup[] = [
  { label: "Watch", sections: [
    { href: "/overview", label: "Overview", icon: LayoutDashboard },
    { href: "/live", label: "Operations", icon: Bot, also: ["/runs", "/documents", "/queues"], children: [
      { href: "/live", label: "Live" },
      { href: "/runs", label: "Agent runs" },
      { href: "/documents", label: "Documents" },
      { href: "/queues", label: "Queues" },
    ] },
  ] },
  { label: "Customers", sections: [
    { href: "/accounts/organizations", label: "Accounts", icon: Building2, also: ["/accounts"], children: [
      { href: "/accounts/organizations", label: "Organizations" },
      { href: "/accounts/people", label: "People" },
    ] },
    { href: "/billing/organizations", label: "Billing", icon: CreditCard, also: ["/billing"], children: [
      // An organization's billing (/billing/<id>) is under Organizations
      { href: "/billing/organizations", label: "Organizations" },
      { href: "/billing/invoices", label: "Invoices" },
      { href: "/billing/payments", label: "Payments to check", count: "payments" },
      { href: "/billing/catalog", label: "Plans and packs" },
      { href: "/billing/settings", label: "Settings" },
    ] },
    { href: "/support", label: "Support", icon: LifeBuoy, children: [
      { href: "/support", label: "Tickets", count: "tickets", also: ["/support/tickets"] },
      { href: "/support/live", label: "Live chats", count: "liveChats" },
      { href: "/support/saved-replies", label: "Saved replies" },
      { href: "/support/settings", label: "Settings" },
    ] },
  ] },
  { label: "Platform", sections: [
    { href: "/platform", label: "Platform controls", icon: SlidersHorizontal, children: [
      { href: "/platform", label: "Switches and maintenance" },
      { href: "/platform/announcements", label: "Announcements" },
      { href: "/platform/lessons", label: "How Syruss works" },
    ] },
    { href: "/email", label: "Email", icon: Mail, permission: "staff.manage", children: [
      { href: "/email", label: "Accounts and purposes", also: ["/email/google"] },
      { href: "/email/log", label: "Delivery log" },
    ] },
    { href: "/staff", label: "Staff", icon: UsersRound, children: [
      { href: "/staff", label: "People" },
      { href: "/staff/roles", label: "Roles" },
      { href: "/staff/invitations", label: "Invitations" },
      { href: "/staff/access", label: "Access review" },
    ] },
    { href: "/security", label: "Security", icon: LockKeyhole },
    { href: "/approvals", label: "Approvals", icon: Hourglass, count: "approvals" },
    { href: "/audit", label: "Audit log", icon: ScrollText },
  ] },
];

const within = (pathname: string, href: string) => pathname === href || pathname.startsWith(`${href}/`);

/** Whether you are in this section. */
export function inSection(section: NavSection, pathname: string): boolean {
  return [section.href, ...(section.also ?? [])].some((h) => within(pathname, h));
}

/**
 * The sub-tab you are on: the one whose address you are at, else the deepest one whose address (or `also`) contains
 * it (an invoice under Invoices). A section's own detail pages that no sub-tab names fall to its first sub-tab
 * (an organization's billing, /billing/<id>, under Organizations).
 */
export function activeChild(section: NavSection, pathname: string): string | undefined {
  const children = section.children ?? [];
  const exact = children.find((c) => c.href === pathname);
  if (exact) return exact.href;
  let best: { href: string; depth: number } | undefined;
  for (const c of children) {
    for (const prefix of [c.href, ...(c.also ?? [])]) {
      if (pathname.startsWith(`${prefix}/`) && (!best || prefix.length > best.depth)) best = { href: c.href, depth: prefix.length };
    }
  }
  if (best) return best.href;
  return inSection(section, pathname) ? children[0]?.href : undefined;
}

/** The section and sub-tab for a path (page headers name where you are). */
export function whereAmI(pathname: string): { section?: NavSection; child?: NavChild } {
  for (const group of NAV) {
    for (const section of group.sections) {
      if (inSection(section, pathname)) {
        const href = activeChild(section, pathname);
        return { section, child: section.children?.find((c) => c.href === href) };
      }
    }
  }
  return {};
}
