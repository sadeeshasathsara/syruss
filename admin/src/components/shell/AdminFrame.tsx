"use client";

import { useQuery } from "@tanstack/react-query";
import { LogOut, Menu, Moon, ShieldCheck, Sun, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { BrandMark } from "@/components/shell/Brand";
import { IdleSignOut } from "@/components/shell/IdleSignOut";
import { Nav } from "@/components/shell/Nav";
import { PresenceProvider } from "@/components/support/Presence";
import { SearchTrigger } from "@/components/shell/CommandPalette";
import { AdminSearch } from "@/components/shell/Search";
import { Button, IconButton } from "@/components/ui/Button";
import { ApiError, api } from "@/lib/api";
import type { StaffMe } from "@/lib/types";
import { resumeAndGo } from "@/lib/resume";

const StaffContext = createContext<StaffMe | null>(null);

/** You, on the platform staff. Only inside the frame, which shows nothing until the API has confirmed it. */
export function useStaff(): StaffMe & { can: (permission: string) => boolean } {
  const me = useContext(StaffContext);
  if (!me) throw new Error("useStaff outside the admin frame");
  return { ...me, can: (permission: string) => me.permissions.includes(permission) };
}

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
  two_step_required: { title: "Sign in with your one-time code", body: "The platform tools need a sign-in with a password and a one-time code from your authenticator app." },
};

function signInAgain() {
  void resumeAndGo(`/api/auth/login?reauth=1&returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
}

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
              {code === "two_step_required" && <Button variant="primary" onClick={signInAgain}>Sign in again</Button>}
              <Button variant={refusal && code !== "two_step_required" ? "primary" : "secondary"} onClick={signOut}>Sign out</Button>
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
  return (
    <Gate>
      {(me) => (
        <StaffContext.Provider value={me}>
          <IdleSignOut minutes={me.session?.idle_minutes ?? 30} onSignOut={signOut} />
          <PresenceProvider enabled={me.permissions.includes("support.respond")}>
            <AdminSearch permissions={me.permissions}><Frame me={me}>{children}</Frame></AdminSearch>
          </PresenceProvider>
        </StaffContext.Provider>
      )}
    </Gate>
  );
}

function Frame({ me, children }: { me: StaffMe; children: React.ReactNode }) {
  const can = (permission: string) => me.permissions.includes(permission);
  const pathname = usePathname();
  // The menu belongs to the page it was opened on: going anywhere closes it. Escape closes it; Tab stays inside it.
  const [menuAt, setMenuAt] = useState<string | null>(null);
  const menu = menuAt === pathname;
  const setMenu = (open: boolean) => setMenuAt(open ? pathname : null);
  const drawer = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    drawer.current?.querySelector<HTMLElement>("a, button")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuAt(null);
      if (e.key !== "Tab" || !drawer.current) return;
      const focusable = [...drawer.current.querySelectorAll<HTMLElement>("a[href]:not([tabindex='-1']), button:not([disabled])")];
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);
  const brand = (
    <div className="flex items-center gap-2 px-1">
      <BrandMark />
      <span className="text-section font-semibold text-ink">Syruss</span>
      <span className="rounded-chip bg-[var(--pill-accent-bg)] px-1.5 text-meta font-medium text-[var(--pill-accent-ink)]">System admin</span>
    </div>
  );
  return (
          <div className="flex h-dvh flex-col overflow-hidden bg-frame lg:flex-row">
            {/* Wide screens: the sidebar */}
            <aside className="hidden w-[236px] flex-none flex-col gap-4 overflow-y-auto px-3 pb-4 pt-3 scrollbar-thin lg:flex">
              <div className="pb-1">{brand}</div>
              <Nav can={can} />
            </aside>

            {/* Narrow screens: a bar with the menu button; the sidebar slides over the page */}
            <div className="flex h-12 flex-none items-center gap-2 border-b border-border px-3 lg:hidden">
              <IconButton aria-label="Open the menu" aria-expanded={menu} aria-controls="admin-menu" onClick={() => setMenu(true)}><Menu /></IconButton>
              {brand}
              <span className="ml-auto flex items-center"><SearchTrigger variant="icon" /><ThemeToggle /><IconButton aria-label="Sign out" onClick={signOut}><LogOut /></IconButton></span>
            </div>
            {menu && (
              <div className="fixed inset-0 z-40 lg:hidden">
                <button type="button" aria-label="Close the menu" tabIndex={-1} className="absolute inset-0 bg-black/40 animate-fade" onClick={() => setMenu(false)} />
                <div ref={drawer} id="admin-menu" role="dialog" aria-modal="true" aria-label="Menu"
                  className="absolute inset-y-0 left-0 flex w-[min(300px,86vw)] flex-col gap-4 overflow-y-auto border-r border-border bg-frame px-3 pb-6 pt-3 shadow-xl">
                  <div className="flex items-center justify-between">{brand}<IconButton aria-label="Close the menu" onClick={() => setMenu(false)}><X /></IconButton></div>
                  <div className="px-1"><SearchTrigger label="Search everything" className="max-w-none" /></div>
                  <Nav can={can} onNavigate={() => setMenu(false)} />
                  <p className="mt-auto px-2.5 text-meta text-muted">{me.name} · {me.role_name}</p>
                </div>
              </div>
            )}

            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <div className="hidden h-12 flex-none items-center gap-2 pr-3 lg:flex">
                <div className="min-w-0 flex-1"><SearchTrigger label="Search organizations, people, documents, tickets" /></div>
                <p className="hidden min-w-0 items-center gap-1.5 text-meta text-muted 2xl:flex" title="Every change you make here is recorded in the audit log.">
                  <ShieldCheck className="size-3.5 flex-none" aria-hidden />
                  <span className="truncate">Changes are audited</span>
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
  );
}
