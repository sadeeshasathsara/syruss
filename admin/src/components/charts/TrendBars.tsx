"use client";

import { useId, useState } from "react";
import { cn } from "@/lib/format";
import type { DayPoint } from "@/lib/types";

const day = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });

/**
 * One measure, a day at a time: thin bars in the accent colour, rounded at the top, on a quiet baseline. The headline is
 * the period's total (or the last value); hovering a day (or focusing the chart and using the arrow keys) reads it out.
 * A table with every day is there for screen readers.
 */
export function TrendBars({
  title, points, format = (v) => v.toLocaleString(), total = "sum", note, className,
}: {
  title: string;
  points?: DayPoint[];
  format?: (value: number) => string;
  total?: "sum" | "last";
  note?: string;
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const id = useId();
  if (!points) {
    return <div className={cn("h-[150px] animate-pulse rounded-card bg-card [box-shadow:var(--shadow-card)]", className)} aria-busy />;
  }
  const max = Math.max(1, ...points.map((p) => p.value));
  const headline = total === "sum" ? points.reduce((a, p) => a + p.value, 0) : points[points.length - 1]?.value ?? 0;
  const shown = hover === null ? null : points[hover];
  const width = 300, height = 64, gap = 2;
  const bar = (width - gap * (points.length - 1)) / points.length;

  return (
    <figure className={cn("flex min-w-0 flex-col gap-2 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]", className)} aria-labelledby={`${id}-t`}>
      <figcaption className="flex items-baseline gap-2">
        <span id={`${id}-t`} className="min-w-0 flex-1 truncate text-body text-muted">{title}</span>
        <span className="num text-meta text-faint" aria-live="polite">{shown ? `${day(shown.day)}: ${format(shown.value)}` : "Last 30 days"}</span>
      </figcaption>
      <span className="num text-figure text-ink">{format(headline)}</span>
      <svg
        viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="h-16 w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]"
        role="img" aria-label={`${title}, ${format(headline)} over 30 days`} tabIndex={0}
        onMouseLeave={() => setHover(null)} onBlur={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? points.length) - 1));
          if (e.key === "ArrowRight") setHover((h) => Math.min(points.length - 1, (h ?? -1) + 1));
        }}
      >
        <line x1={0} x2={width} y1={height - 0.5} y2={height - 0.5} stroke="var(--rule)" strokeWidth={1} />
        {points.map((p, i) => {
          const h = p.value ? Math.max(2, (p.value / max) * (height - 4)) : 0;
          const x = i * (bar + gap);
          return (
            <g key={p.day} onMouseEnter={() => setHover(i)}>
              <rect x={x} y={0} width={bar + gap} height={height} fill="transparent" />{/* the hover target: the whole column */}
              {h > 0 && <rect x={x} y={height - h} width={bar} height={h} rx={Math.min(2, bar / 2)} fill="var(--accent)" opacity={hover === null || hover === i ? 1 : 0.45} />}
            </g>
          );
        })}
      </svg>
      {note && <span className="text-meta text-muted">{note}</span>}
      <table className="sr-only">
        <caption>{title}, by day</caption>
        <tbody>{points.map((p) => <tr key={p.day}><th scope="row">{day(p.day)}</th><td>{format(p.value)}</td></tr>)}</tbody>
      </table>
    </figure>
  );
}
