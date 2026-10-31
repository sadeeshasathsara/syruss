"use client";

import { Menu, MessageSquareText, PanelRightClose } from "lucide-react";
import { IconButton, Button } from "@/components/ui/Button";
import { Sheet, SheetClose } from "@/components/ui/Sheet";
import { cn } from "@/lib/format";
import { NotificationBell, NotificationToasts } from "./NotificationBell";
import { OfflineBanner } from "./OfflineBanner";
import { useShell } from "./ShellContext";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";

/**
 * The app frame (style: framed sheet).
 *  ≥1280px  tinted frame: sidebar | work sheet | docked chat sheet. Closing the chat gives the
 *           width back to the work; nothing is squeezed below its readable size.
 *  1024+    sidebar | work sheet; the chat slides over from the right.
 *  <1024    a 48px top bar; navigation and chat are full-height sheets. The work fills the screen.
 */
export function AppFrame({
  sidebar,
  topbar,
  title,
  chat,
  chatTabs,
  children,
}: {
  sidebar: React.ReactNode;
  topbar?: React.ReactNode;
  title: string;
  chat?: React.ReactNode;
  /** Shown in the chat panel's header, beside its close button (e.g. Chat / Activity). */
  chatTabs?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { docked, chatOpen, setChatOpen, navOpen, setNavOpen } = useShell();
  const showDocked = !!chat && docked && chatOpen;

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden">
      <OfflineBanner />
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
        {/* Sidebar (≥1024) */}
        <aside className="hidden w-[228px] flex-none flex-col gap-4 overflow-y-auto px-3 pb-3 pt-2 scrollbar-thin lg:flex">
          {sidebar}
        </aside>

        {/* Compact top bar (<1024) */}
        <header className="flex h-12 flex-none items-center gap-1 border-b border-border bg-frame px-2 lg:hidden">
          <IconButton aria-label="Open navigation" onClick={() => setNavOpen(true)}>
            <Menu />
          </IconButton>
          <span className="min-w-0 flex-1 truncate px-1 text-body font-semibold text-ink">{title}</span>
          <NotificationBell />
          {chat && (
            <IconButton aria-label="Open Syruss AI" onClick={() => setChatOpen(true)}>
              <MessageSquareText />
            </IconButton>
          )}
          <UserMenu />
        </header>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {/* Top bar of the frame (≥1024): scope on the left, utilities on the right, no rule. */}
          <div className="hidden h-12 flex-none items-center gap-2 pr-3 lg:flex">
            <div className="min-w-0 flex-1">{topbar}</div>
            <ThemeToggle />
            {chat && !showDocked && (
              <Button variant="secondary" size="sm" onClick={() => setChatOpen(true)}>
                <MessageSquareText /> Syruss AI
              </Button>
            )}
            <NotificationBell />
            <UserMenu />
          </div>

          <div className="flex min-h-0 flex-1 gap-2 lg:pb-2 lg:pr-2">
            <main
              id="main"
              className="@container relative min-h-0 min-w-0 flex-1 overflow-y-auto bg-well scrollbar-thin lg:rounded-sheet lg:border lg:border-border"
            >
              <div className="mx-auto w-full max-w-[1280px] px-4 py-5 sm:px-6 sm:py-6">{children}</div>
            </main>

            {showDocked && (
              <section
                aria-label="Syruss AI chat"
                data-chat-panel
                className="relative flex w-[clamp(340px,28vw,420px)] flex-none flex-col overflow-hidden rounded-sheet border border-border bg-chat-panel"
              >
                <div className="flex h-12 flex-none items-center gap-2 border-b border-border pl-4 pr-1.5">
                  <h2 className="min-w-0 flex-1 truncate text-body font-semibold text-ink">Syruss AI</h2>
                  {chatTabs}
                  <IconButton aria-label="Close the agent chat" onClick={() => setChatOpen(false)}>
                    <PanelRightClose />
                  </IconButton>
                </div>
                {chat}
              </section>
            )}
          </div>
        </div>

        {/* Navigation sheet (<1024) */}
        <Sheet open={navOpen} onOpenChange={setNavOpen} side="left" title="Navigation" hideTitle>
          <div className="flex items-center justify-end px-2 pt-2">
            <ThemeToggle />
            <SheetClose label="Close navigation" />
          </div>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-3 pb-4">{sidebar}</div>
        </Sheet>

        {/* Chat sheet when it cannot be docked */}
        {chat && !docked && (
          <Sheet open={chatOpen} onOpenChange={setChatOpen} side="right" title="Syruss AI" hideTitle className={cn("bg-chat-panel sm:max-w-[440px]")}>
            <div className="flex h-12 flex-none items-center gap-2 border-b border-border pl-4 pr-1.5">
              <h2 className="min-w-0 flex-1 truncate text-body font-semibold text-ink">Syruss AI</h2>
              {chatTabs}
              <SheetClose label="Close the agent chat" />
            </div>
            {chat}
          </Sheet>
        )}
      </div>
    <NotificationToasts />
    </div>
  );
}
