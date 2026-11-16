"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";

const KEY = "syruss-admin-last-activity";
const WARN_SECONDS = 120;
const ACTIVITY = ["pointerdown", "keydown", "wheel", "touchstart", "mousemove"] as const;

function stored(): number {
  try {
    return Number(localStorage.getItem(KEY)) || 0;
  } catch {
    return 0;
  }
}

function remember(at: number) {
  try {
    localStorage.setItem(KEY, String(at));
  } catch {
    /* storage unavailable: this tab keeps its own count */
  }
}

/**
 * Signs out after `minutes` without anyone using the admin app, in any of its tabs (they share the time of the last
 * activity), with a warning two minutes before. Pages that refresh themselves don't count as use: only a person does.
 */
export function IdleSignOut({ minutes, onSignOut }: { minutes: number; onSignOut: () => void }) {
  const local = useRef(0);
  const [left, setLeft] = useState<number | null>(null);

  useEffect(() => {
    const start = Date.now();
    local.current = start;
    remember(start);
    let written = start;
    const touch = () => {
      const now = Date.now();
      local.current = now;
      if (now - written > 10_000) {  // a mouse moves often: write it down every ten seconds at most
        written = now;
        remember(now);
      }
    };
    ACTIVITY.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const timer = window.setInterval(() => {
      const last = Math.max(local.current, stored());
      const remaining = Math.round((last + minutes * 60_000 - Date.now()) / 1000);
      if (remaining <= 0) {
        window.clearInterval(timer);
        onSignOut();
      } else {
        setLeft(remaining <= WARN_SECONDS ? remaining : null);
      }
    }, 1000);
    return () => {
      window.clearInterval(timer);
      ACTIVITY.forEach((e) => window.removeEventListener(e, touch));
    };
  }, [minutes, onSignOut]);

  if (left === null) return null;
  const stay = () => {
    local.current = Date.now();
    remember(local.current);
    setLeft(null);
  };
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="idle-title" aria-describedby="idle-body"
         className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="flex w-full max-w-sm flex-col gap-3 rounded-sheet border border-border bg-sheet p-5 [box-shadow:var(--shadow-card)]">
        <h2 id="idle-title" className="text-section font-semibold text-ink">Still there?</h2>
        <p id="idle-body" className="text-body text-muted">
          For safety, the admin app signs out after {minutes} minutes without use. You&apos;ll be signed out in{" "}
          <span className="num font-medium text-ink">{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}</span>.
        </p>
        <div className="flex justify-end gap-2">
          <Button onClick={onSignOut}>Sign out now</Button>
          <Button variant="primary" onClick={stay} autoFocus>Stay signed in</Button>
        </div>
      </div>
    </div>
  );
}
