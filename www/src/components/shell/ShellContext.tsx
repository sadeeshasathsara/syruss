"use client";

import { createContext, useCallback, useContext, useState } from "react";
import { getActiveConversation } from "@/lib/api";
import { storeValue, useMediaQuery, useStoredValue } from "@/lib/browser";

export interface ActionNote {
  id: string;
  text: string;
  href?: string;
  at: string;
  /** The chat that was open when it happened; the note shows there. */
  conversationId?: string;
}

export type ChatTab = "chat" | "activity";

interface ShellContextValue {
  /** True when the chat fits beside the work (≥1280px) instead of sliding over it. */
  docked: boolean;
  chatOpen: boolean;
  setChatOpen: (open: boolean, tab?: ChatTab) => void;
  /** The panel's tab: your conversation, or the workspace's activity. */
  chatTab: ChatTab;
  setChatTab: (tab: ChatTab) => void;
  navOpen: boolean;
  setNavOpen: (open: boolean) => void;
  /** Things the user did, shown in the chat they did them in so their outcome can be followed there. */
  notes: ActionNote[];
  announce: (text: string, opts?: { href?: string; openChat?: boolean }) => void;
}

const ShellContext = createContext<ShellContextValue | null>(null);
const CHAT_KEY = "chat-docked-open";

export function ShellProvider({ children }: { children: React.ReactNode }) {
  const docked = useMediaQuery("(min-width: 1280px)");
  const savedDocked = useStoredValue(CHAT_KEY);
  // The docked chat remembers being closed; the slide-over always starts closed.
  const [overlayOpen, setOverlayOpen] = useState(false);
  const chatOpen = docked ? savedDocked !== "0" : overlayOpen;
  const [chatTab, setChatTab] = useState<ChatTab>("chat");
  const [navOpen, setNavOpen] = useState(false);
  const [notes, setNotes] = useState<ActionNote[]>([]);

  const setChatOpen = useCallback(
    (open: boolean, tab?: ChatTab) => {
      if (tab) setChatTab(tab);
      if (docked) storeValue(CHAT_KEY, open ? "1" : "0");
      else setOverlayOpen(open);
    },
    [docked],
  );

  const announce = useCallback(
    (text: string, opts?: { href?: string; openChat?: boolean }) => {
      setNotes((prev) => [
        ...prev,
        { id: `note_${Date.now()}_${prev.length}`, text, href: opts?.href, at: new Date().toISOString(), conversationId: getActiveConversation() },
      ]);
      if (opts?.openChat) setChatOpen(true);
    },
    [setChatOpen],
  );

  return (
    <ShellContext.Provider value={{ docked, chatOpen, setChatOpen, chatTab, setChatTab, navOpen, setNavOpen, notes, announce }}>
      {children}
    </ShellContext.Provider>
  );
}

export function useShell(): ShellContextValue {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error("useShell must be used inside the app shell");
  return ctx;
}
