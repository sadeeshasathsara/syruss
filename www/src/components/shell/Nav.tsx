"use client";

import * as Menu from "@radix-ui/react-dropdown-menu";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronRight, ChevronsUpDown, Plus } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { ComponentType } from "react";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { Workspace } from "@/lib/types";
import { useShell } from "./ShellContext";

export interface NavChild {
  href: string;
  label: string;
  count?: number;
}

export interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** Only for queues that need action: "Review 3". */
  count?: number;
  /** A small word beside the name, such as "Beta" for a part still being shaped. */
  badge?: string;
  exact?: boolean;
  /** Paths that also make this item active (e.g. a sub tab that lives on another route). */
  also?: string[];
  /** Sub tabs, shown under the item while you are in it. */
  children?: NavChild[];
}

/** The sub tab that best matches where you are: same path, and every query value it names. */
function activeChild(children: NavChild[], pathname: string, search: URLSearchParams): string | undefined {
  let best: { href: string; score: number } | undefined;
  for (const c of children) {
    const url = new URL(c.href, "http://x");
    if (url.pathname !== pathname) continue;
    const wanted = [...url.searchParams];
    if (!wanted.every(([k, v]) => search.get(k) === v)) continue;
    if (!best || wanted.length > best.score) best = { href: c.href, score: wanted.length };
  }
  return best?.href;
}

export function NavList({ items, label }: { items: NavItem[]; label: string }) {
  const pathname = usePathname();
  const search = useSearchParams();
  const { setNavOpen } = useShell();
  const within = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  return (
    <nav aria-label={label}>
      <ul className="flex flex-col gap-0.5">
        {items.map((item) => {
          const path = item.href.split("?")[0];
          const active = item.exact ? pathname === path : within(path) || (item.also ?? []).some(within);
          const current = active && item.children ? activeChild(item.children, pathname, search) : undefined;
          const Icon = item.icon;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={() => setNavOpen(false)}
                aria-current={active && !item.children ? "page" : undefined}
                aria-expanded={item.children ? active : undefined}
                className={cn(
                  "flex h-8 items-center gap-2.5 rounded-control px-2.5 text-body transition-colors pointer-coarse:h-11",
                  active ? "bg-active font-medium text-ink" : "text-muted hover:bg-active/60 hover:text-ink",
                )}
              >
                <Icon className="size-4 flex-none" />
                <span className="min-w-0 truncate">{item.label}</span>
                {item.badge && <span className="flex-none rounded-chip bg-[var(--pill-info-bg)] px-1.5 text-[11px] font-medium leading-4 text-[var(--pill-info-ink)]">{item.badge}</span>}
                <span className="flex-1" />
                {!!item.count && <span className="num text-meta font-medium text-ink">{item.count}</span>}
                {item.children && (
                  <ChevronRight aria-hidden className={cn("size-3.5 flex-none text-faint transition-transform duration-200", active && "rotate-90")} />
                )}
              </Link>
              {item.children && (
                <div className="collapsible" data-open={active}>
                  <div>
                    <ul className="ml-[17px] mt-0.5 flex flex-col gap-0.5 border-l border-border pb-1 pl-2">
                      {item.children.map((c) => (
                        <li key={c.href}>
                          <Link
                            href={c.href}
                            tabIndex={active ? undefined : -1}
                            onClick={() => setNavOpen(false)}
                            aria-current={current === c.href ? "page" : undefined}
                            className={cn(
                              "flex h-7 items-center gap-2 rounded-control px-2 text-body transition-colors pointer-coarse:h-10",
                              current === c.href ? "font-medium text-ink" : "text-muted hover:bg-active/60 hover:text-ink",
                            )}
                          >
                            <span className="min-w-0 flex-1 truncate">{c.label}</span>
                            {!!c.count && <span className="num text-meta text-muted">{c.count}</span>}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Scope switcher: which workspace you are in, and the way to every other one. */
export function WorkspaceSwitcher({ org, current }: { org: string; current?: Workspace }) {
  const { data: all } = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces.list });
  const workspaces = (all ?? []).filter((w) => w.my_role);
  const { canOrg } = useMe();
  const { setNavOpen } = useShell();
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <button
          type="button"
          className="flex h-9 w-full items-center gap-2 rounded-control border border-border bg-sheet px-2.5 text-left text-body hover:border-control pointer-coarse:h-11"
        >
          <span className="min-w-0 flex-1 truncate font-medium text-ink">{current?.name ?? "All workspaces"}</span>
          <ChevronsUpDown className="size-4 flex-none text-muted" />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="start"
          sideOffset={6}
          className="z-50 w-[var(--radix-dropdown-menu-trigger-width)] min-w-56 rounded-card border border-border bg-sheet p-1 shadow-float"
        >
          <Menu.Item asChild>
            <Link href={`/${org}/workspaces`} onClick={() => setNavOpen(false)} className="flex h-8 items-center rounded-control px-2 text-body text-muted outline-none data-[highlighted]:bg-raised data-[highlighted]:text-ink">
              All workspaces
            </Link>
          </Menu.Item>
          {workspaces.map((w) => (
            <Menu.Item key={w.id} asChild>
              <Link
                href={`/${org}/w/${w.slug}`}
                onClick={() => setNavOpen(false)}
                className="flex h-8 items-center gap-2 rounded-control px-2 text-body text-ink outline-none data-[highlighted]:bg-raised"
              >
                <span className="min-w-0 flex-1 truncate">{w.name}</span>
                {w.status === "draft" && <span className="text-meta text-muted">Draft</span>}
                {current?.id === w.id && <Check className="size-4 text-ink" />}
              </Link>
            </Menu.Item>
          ))}
          {canOrg("workspaces.create") && (
            <>
              <Menu.Separator className="my-1 h-px bg-border" />
              <Menu.Item asChild>
                <Link href={`/${org}/new`} onClick={() => setNavOpen(false)} className="flex h-8 items-center gap-2 rounded-control px-2 text-body text-ink outline-none data-[highlighted]:bg-raised">
                  <Plus className="size-4" /> New workspace
                </Link>
              </Menu.Item>
            </>
          )}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

/**
 * Which organization you are in, and the way to every other one you belong to (one you were invited to, one you made).
 * Each organization is its own place with its own workspaces, so switching loads it fresh.
 */
export function OrgSwitcher({ org }: { org: string }) {
  const { data: orgs } = useQuery({ queryKey: ["my-organizations"], queryFn: api.me.organizations, staleTime: 30_000, refetchOnWindowFocus: true });
  const current = orgs?.find((o) => o.slug === org);
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <button
          type="button"
          aria-label="Switch organization"
          className="flex h-9 w-full items-center gap-2 rounded-control px-2.5 text-left text-body hover:bg-active/60 pointer-coarse:h-11"
        >
          <span className="grid size-5 flex-none place-items-center rounded-chip bg-primary text-meta font-semibold text-primary-ink">
            {(current?.name ?? org).slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0 flex-1 truncate font-medium text-ink">{current?.name ?? org}</span>
          <ChevronsUpDown className="size-4 flex-none text-muted" />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="start"
          sideOffset={6}
          className="z-50 w-[var(--radix-dropdown-menu-trigger-width)] min-w-60 rounded-card border border-border bg-sheet p-1 shadow-float"
        >
          <p className="px-2 pb-1 pt-1.5 text-meta font-medium text-muted">Your organizations</p>
          {(orgs ?? []).map((o) => (
            <Menu.Item key={o.id} asChild>
              {/* A full page load, so nothing from the other organization is carried across. */}
              <a href={`/${o.slug}`} className="flex min-h-9 items-center gap-2 rounded-control px-2 py-1 text-body text-ink outline-none data-[highlighted]:bg-raised">
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{o.name}</span>
                  <span className="block truncate text-meta text-muted">{o.role_label}</span>
                </span>
                {o.slug === org && <Check className="size-4 flex-none text-ink" />}
              </a>
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
