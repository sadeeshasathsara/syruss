"use client";

import { useQuery } from "@tanstack/react-query";
import { Info, Megaphone, TriangleAlert, Wrench, X } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";

const KEY = "syruss-dismissed-announcements";
const TONE = {
  info: { box: "bg-[var(--pill-info-bg)] text-[var(--pill-info-ink)]", icon: Info },
  warning: { box: "bg-[var(--pill-warning-bg)] text-[var(--pill-warning-ink)]", icon: Megaphone },
  critical: { box: "bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]", icon: TriangleAlert },
} as const;

function dismissedBefore(): string[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "[]");
  } catch {
    return [];
  }
}

/**
 * What Syruss is telling everyone right now, at the top of every page: maintenance (changes paused) and announcements
 * from the Syruss team. Information and warnings can be dismissed on this device; critical ones and maintenance stay.
 */
export function Announcements() {
  const told = useQuery({ queryKey: ["announcements"], queryFn: api.announcements, refetchInterval: 2 * 60_000, retry: false });
  const [dismissed, setDismissed] = useState<string[]>(() => (typeof window === "undefined" ? [] : dismissedBefore()));
  const dismiss = (id: string) => {
    const next = [...dismissed, id].slice(-50);
    setDismissed(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable: it comes back on the next visit */
    }
  };
  const data = told.data;
  if (!data) return null;
  const items = data.items.filter((a) => a.severity === "critical" || !dismissed.includes(a.id));
  if (!data.maintenance && !items.length) return null;
  return (
    <div className="mb-4 flex flex-col gap-2" aria-live="polite">
      {data.maintenance && (
        <div role="status" className="flex items-start gap-2 rounded-card bg-[var(--pill-warning-bg)] px-4 py-3 text-body text-[var(--pill-warning-ink)]">
          <Wrench className="mt-0.5 size-4 flex-none" aria-hidden />
          <p className="min-w-0 flex-1"><span className="font-medium">Maintenance. </span>{data.maintenance.message}</p>
        </div>
      )}
      {items.map((a) => {
        const tone = TONE[a.severity] ?? TONE.info;
        const Icon = tone.icon;
        return (
          <div key={a.id} role={a.severity === "critical" ? "alert" : "status"} className={cn("flex items-start gap-2 rounded-card px-4 py-3 text-body", tone.box)}>
            <Icon className="mt-0.5 size-4 flex-none" aria-hidden />
            <p className="min-w-0 flex-1"><span className="font-medium">{a.title}</span>{a.body ? ` ${a.body}` : ""}</p>
            {a.severity !== "critical" && (
              <button type="button" onClick={() => dismiss(a.id)} aria-label="Dismiss" className="-mr-1 rounded-control p-1 opacity-70 hover:opacity-100">
                <X className="size-4" />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
