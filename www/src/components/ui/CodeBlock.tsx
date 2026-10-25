"use client";

import { useState } from "react";
import { cn } from "@/lib/format";
import { CopyButton } from "./CopyButton";

export interface Sample {
  /** The tab: "curl", "Python", "Node". */
  label: string;
  code: string;
}

/** Code to copy: one block, or tabs when the same thing is shown in several languages. */
export function CodeBlock({ samples, className }: { samples: Sample[]; className?: string }) {
  const [index, setIndex] = useState(0);
  const current = samples[Math.min(index, samples.length - 1)];
  return (
    <div className={cn("min-w-0 overflow-hidden rounded-card border border-border bg-raised", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-border px-2 py-1.5">
        <div role="tablist" aria-label="Language" className="flex gap-1">
          {samples.length > 1 ? samples.map((s, i) => (
            <button
              key={s.label}
              role="tab"
              type="button"
              aria-selected={i === index}
              onClick={() => setIndex(i)}
              className={cn(
                "h-7 rounded-control px-2.5 text-meta font-medium transition-colors pointer-coarse:h-9",
                i === index ? "bg-sheet text-ink [box-shadow:var(--shadow-card)]" : "text-muted hover:text-ink",
              )}
            >
              {s.label}
            </button>
          )) : <span className="px-2 text-meta font-medium text-muted">{current.label}</span>}
        </div>
        <CopyButton value={current.code} size="sm" />
      </div>
      <pre className="scrollbar-thin overflow-x-auto p-4 text-meta leading-relaxed text-ink"><code className="font-mono">{current.code}</code></pre>
    </div>
  );
}
