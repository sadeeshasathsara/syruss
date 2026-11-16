"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { APPROVAL_EVENT, api } from "@/lib/api";
import { cn } from "@/lib/format";
import { NAV, type CountKey, type NavChild, type NavSection, activeChild, inSection } from "@/lib/nav";

/**
 * The work waiting behind the navigation's counts: tickets nobody has taken or that are late, chats waiting for a person,
 * payment slips to check, and risky actions waiting for a second owner.
 */
export function useNavCounts(): Partial<Record<CountKey, number>> {
  const queryClient = useQueryClient();
  const support = useQuery({ queryKey: ["support", "summary"], queryFn: api.support.summary, refetchInterval: 60_000 });
  const approvals = useQuery({ queryKey: ["approvals", "pending"], queryFn: () => api.approvals.list("pending"), refetchInterval: 60_000 });
  const payments = useQuery({ queryKey: ["billing", "submissions", "submitted"], queryFn: () => api.billing.submissions("submitted"),
    refetchInterval: 60_000, retry: false });
  useEffect(() => {
    const refresh = () => queryClient.invalidateQueries({ queryKey: ["approvals"] });
    window.addEventListener(APPROVAL_EVENT, refresh);
    return () => window.removeEventListener(APPROVAL_EVENT, refresh);
  }, [queryClient]);
  return {
    tickets: (support.data?.unassigned ?? 0) + (support.data?.breached ?? 0),
    liveChats: support.data?.live_waiting ?? 0,
    payments: payments.data?.length ?? 0,
    approvals: approvals.data?.waiting ?? 0,
  };
}

function Count({ n, strong }: { n?: number; strong?: boolean }) {
  if (!n) return null;
  return (
    <span className={cn("num flex-none rounded-chip px-1.5 text-meta font-medium",
      strong ? "bg-[var(--pill-warning-bg)] text-[var(--pill-warning-ink)]" : "text-[var(--pill-warning-ink)]")} aria-label={`${n} waiting`}>
      {n}
    </span>
  );
}

/** The admin app's sidebar: groups of sections; the section you are in opens to show its sub-tabs. */
export function Nav({ can, onNavigate }: { can: (permission: string) => boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const counts = useNavCounts();
  const visible = (item: { permission?: string }) => !item.permission || can(item.permission);
  return (
    <nav aria-label="Platform tools" className="flex flex-col gap-4">
      {NAV.map((group) => {
        const sections = group.sections.filter(visible);
        if (!sections.length) return null;
        return (
          <div key={group.label} className="flex flex-col gap-0.5">
            <p className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wide text-faint">{group.label}</p>
            <ul className="flex flex-col gap-0.5">
              {sections.map((s) => <Section key={s.href} section={s} pathname={pathname} counts={counts} visible={visible} onNavigate={onNavigate} />)}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function Section({ section: s, pathname, counts, visible, onNavigate }: {
  section: NavSection; pathname: string; counts: Partial<Record<CountKey, number>>; visible: (item: { permission?: string }) => boolean;
  onNavigate?: () => void;
}) {
  const active = inSection(s, pathname);
  const children = (s.children ?? []).filter(visible);
  const current = active && children.length ? activeChild({ ...s, children }, pathname) : undefined;
  // A closed section shows the work waiting in all its sub-tabs; an open one shows it on each sub-tab
  const total = s.count ? counts[s.count] : children.reduce((sum, c: NavChild) => sum + (c.count ? counts[c.count] ?? 0 : 0), 0);
  const Icon = s.icon;
  return (
    <li>
      <Link
        href={s.href}
        onClick={onNavigate}
        aria-current={active && !children.length ? "page" : undefined}
        aria-expanded={children.length ? active : undefined}
        className={cn(
          "flex h-8 items-center gap-2.5 rounded-control px-2.5 text-body transition-colors pointer-coarse:h-11",
          active ? "bg-active font-medium text-ink" : "text-muted hover:bg-active/60 hover:text-ink",
        )}
      >
        <Icon className="size-4 flex-none" />
        <span className="min-w-0 truncate">{s.label}</span>
        <span className="flex-1" />
        {(!children.length || !active) && <Count n={total} strong />}
        {!!children.length && <ChevronRight aria-hidden className={cn("size-3.5 flex-none text-faint transition-transform duration-200", active && "rotate-90")} />}
      </Link>
      {!!children.length && (
        <div className="collapsible" data-open={active}>
          <div>
            <ul className="ml-[17px] mt-0.5 flex flex-col gap-0.5 border-l border-border pb-1 pl-2">
              {children.map((c) => (
                <li key={c.href}>
                  <Link
                    href={c.href}
                    tabIndex={active ? undefined : -1}
                    onClick={onNavigate}
                    aria-current={current === c.href ? "page" : undefined}
                    className={cn(
                      "flex h-7 items-center gap-2 rounded-control px-2 text-body transition-colors pointer-coarse:h-10",
                      current === c.href ? "font-medium text-ink" : "text-muted hover:bg-active/60 hover:text-ink",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate">{c.label}</span>
                    <Count n={c.count ? counts[c.count] : undefined} />
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </li>
  );
}
