"use client";

import { WifiOff } from "lucide-react";
import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** Says so when the browser has no internet connection; gone again once it's back. */
export function OfflineBanner() {
  const online = useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
  if (online) return null;
  return (
    <div role="status" className="flex flex-none animate-fade items-center justify-center gap-2 bg-[var(--pill-warning-bg)] px-4 py-2 text-body text-[var(--pill-warning-ink)]">
      <WifiOff className="size-4 flex-none" />
      <span>You’re not connected to the internet. Changes won’t be saved until you’re back online.</span>
    </div>
  );
}
