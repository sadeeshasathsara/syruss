"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Presence } from "@/lib/types";

const HEARTBEAT_MS = 30_000;

const PresenceContext = createContext<{ status: Presence; set: (status: Presence) => Promise<void> } | null>(null);

/**
 * Whether you take live chats. While you are online or away the app tells the server every 30 seconds, from any page;
 * when the app is closed the heartbeat stops and the server counts you offline within a minute and a half.
 */
export function PresenceProvider({ enabled, children }: { enabled: boolean; children: React.ReactNode }) {
  const [status, setStatus] = useState<Presence>("offline");
  useEffect(() => {
    if (!enabled) return;
    api.support.live.presence().then((r) => setStatus(r.status)).catch(() => undefined);
  }, [enabled]);
  useEffect(() => {
    if (!enabled || status === "offline") return;
    const timer = setInterval(() => api.support.live.setPresence(status).catch(() => undefined), HEARTBEAT_MS);
    return () => clearInterval(timer);
  }, [enabled, status]);
  const set = useCallback(async (next: Presence) => {
    await api.support.live.setPresence(next);
    setStatus(next);
  }, []);
  return <PresenceContext.Provider value={{ status, set }}>{children}</PresenceContext.Provider>;
}

export function usePresence() {
  const ctx = useContext(PresenceContext);
  if (!ctx) throw new Error("usePresence must be used inside the admin frame");
  return ctx;
}
