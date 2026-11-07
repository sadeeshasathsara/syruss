"use client";

import { useQuery } from "@tanstack/react-query";
import { Bot, CreditCard, FileText, LayoutDashboard, Layers, LogOut, Moon, Radio, ScrollText, ShieldCheck, Sun, UsersRound } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useSyncExternalStore } from "react";
import { BrandMark } from "@/components/shell/Brand";
import { Button, IconButton } from "@/components/ui/Button";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { StaffMe } from "@/lib/types";

const StaffContext = createContext<StaffMe | null>(null);

/** You, on the platform staff. Only inside the frame, which shows nothing until the API has confirmed it. */
export function useStaff(): StaffMe & { can: (permission: string) => boolean } {
  const me = useContext(StaffContext);
  if (!me) throw new Error("useStaff outside the admin frame");
  return { ...me, can: (permission: string) => me.permissions.includes(permission) };
}

const NAV = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard },
  { href: "/live", label: "Live", icon: Radio },
  { href: "/runs", label: "Agent runs", icon: Bot },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/queues", label: "Queues", icon: Layers },
  { href: "/billing", label: "Billing", icon: CreditCard },
  { href: "/staff", label: "Staff", icon: UsersRound },
  { href: "/audit", label: "Audit log", icon: ScrollText },
];

// Signing out ends the session on this app's server and at Keycloak, so it is a real form post, not a fetch.
function signOut() {
  const form = document.createElement("form");
  form.method = "post";
  form.action = "/api/auth/logout";
  document.body.appendChild(form);
  form.submit();
}

function useTheme(): "light" | "dark" {
  return useSyncExternalStore(
    (onChange) => {
      const observer = new MutationObserver(onChange);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      return () => observer.disconnect();
    },
    () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"),
    () => "light",
  );
}

function ThemeToggle() {
  const theme = useTheme();
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    if (next === "dark") document.documentElement.dataset.theme = "dark";
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* storage unavailable: the preference just isn't remembered */
    }
  };
  return (
    <IconButton aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"} onClick={toggle}>
      {theme === "dark" ? <Sun /> : <Moon />}
    </IconButton>
  );
}

const REFUSALS: Record<string, { title: string; body: string }> = {
  platform_staff_required: { title: "Not on the platform staff", body: "This app is for the people who run Syruss. If you should have access, ask a platform owner to add you." },
  account_disabled: { title: "This account is disabled", body: "A disabled account can't use the platform tools." },
  admin_app_required: { title: "Sign in again", body: "Your session came from another Syruss app. Sign in to the admin app itself." },
  device_unverified: { title: "Confirm this browser first", body: "Sign in to the Syruss app once from this browser to confirm it with the emailed code, then come back here." },
};

function Gate({ children }: { children: (me: StaffMe) => React.ReactNode }) {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 5 * 60_000, retry: false });
  if (me.data) return <>{children(me.data)}</>;
  const code = me.error instanceof ApiError ? me.error.code : undefined;
  const refusal = code ? REFUSALS[code] : undefined;
  return (
    <div className="flex h-dvh items-center justify-center bg-frame px-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-3 rounded-sheet border border-border bg-sheet px-6 py-8 text-center">
        <BrandMark size={28} />
        {me.isLoading ? (
          <p className="text-body text-muted">Checking your access…</p>
        ) : (
          <>
            <h1 className="text-section font-semibold text-ink">{refusal?.title ?? "Couldn't open the platform tools"}</h1>
            <p className="text-body text-muted">{refusal?.body ?? (me.error as Error | null)?.message ?? "Try again in a moment."}</p>
            <div className="mt-2 flex gap-2">
              {!refusal && <Button onClick={() => me.refetch()}>Try again</Button>}
              <Button variant={refusal ? "primary" : "secondary"} onClick={signOut}>Sign out</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The admin frame: a sidebar of the platform tools, a top bar with who you are, and the page. Nothing renders until the
 * API confirms you're on the platform staff; every call is checked again on the server.
 */
export function AdminFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <Gate>
      {(me) => (
        <StaffContext.Provider value={me}>
          <div className="flex h-dvh flex-col overflow-hidden bg-frame lg:flex-row">
            <aside className="flex flex-none flex-col gap-1 border-b border-border px-3 py-2 lg:w-[228px] lg:border-b-0 lg:pt-3">
              <div className="flex items-center gap-2 px-1 pb-1 lg:pb-3">
                <BrandMark />
                <span className="text-section font-semibold text-ink">Syruss</span>
                <span className="rounded-chip bg-[var(--pill-accent-bg)] px-1.5 text-meta font-medium text-[var(--pill-accent-ink)]">System admin</span>
                <span className="ml-auto flex items-center lg:hidden"><ThemeToggle /><IconButton aria-label="Sign out" onClick={signOut}><LogOut /></IconButton></span>
              </div>
              <nav aria-label="Platform tools" className="-mx-3 overflow-x-auto px-3 lg:mx-0 lg:px-0">
                <ul className="flex gap-1 lg:flex-col">
                  {NAV.map(({ href, label, icon: Icon }) => {
                    const active = path === href || path.startsWith(`${href}/`);
                    return (
                      <li key={href} className="flex-none">
                        <Link
                          href={href}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "flex h-8 items-center gap-2 rounded-control px-2.5 text-body transition-colors pointer-coarse:h-10",
                            active ? "bg-active font-medium text-ink" : "text-muted hover:bg-raised hover:text-ink",
                          )}
                        >
                          <Icon className="size-4" aria-hidden /> {label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </nav>
            </aside>

            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <div className="hidden h-12 flex-none items-center gap-2 pr-3 lg:flex">
                <p className="flex min-w-0 flex-1 items-center gap-1.5 text-meta text-muted">
                  <ShieldCheck className="size-3.5 flex-none" aria-hidden />
                  <span className="truncate">Every change you make here is recorded in the audit log.</span>
                </p>
                <ThemeToggle />
                <div className="flex items-center gap-2 pl-1 text-right">
                  <span className="leading-tight">
                    <span className="block text-meta font-medium text-ink">{me.name}</span>
                    <span className="block text-meta text-muted">{me.role_name}{me.from_config ? " (set by the deployment)" : ""}</span>
                  </span>
                  <IconButton aria-label="Sign out" onClick={signOut}><LogOut /></IconButton>
                </div>
              </div>
              <main id="main" className="@container relative min-h-0 min-w-0 flex-1 overflow-y-auto bg-well scrollbar-thin lg:mb-2 lg:mr-2 lg:rounded-sheet lg:border lg:border-border">
                <div className="mx-auto w-full max-w-[1280px] px-4 py-5 sm:px-6 sm:py-6">{children}</div>
              </main>
            </div>
          </div>
        </StaffContext.Provider>
      )}
    </Gate>
  );
}
