import { cn } from "@/lib/format";
import type { Overview } from "@/lib/types";

/** "42 s", "3 min", "1.4 h". */
export function duration(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/** "$0.42", "$12.30", "$1,240". */
export function money(usd: number): string {
  if (usd > 0 && usd < 0.01) return "< $0.01";
  return usd.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: usd >= 1000 ? 0 : 2 });
}

/** "950", "12k", "4.7M". */
export function compact(n: number): string {
  return n.toLocaleString(undefined, { notation: "compact", maximumFractionDigits: 1 });
}

/** Used against a limit, turning amber then red as it fills. A null limit is unlimited: the bar stays empty. */
export function Meter({ label, used, limit, format = (n) => n.toLocaleString() }: { label: string; used: number; limit: number | null; format?: (n: number) => string }) {
  const share = limit ? Math.min(1, used / limit) : 0;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-3 text-body">
        <span className="min-w-0 truncate text-muted">{label}</span>
        <span className="num flex-none text-ink">{format(used)} <span className="text-muted">{limit == null ? "· unlimited" : `of ${format(limit)}`}</span></span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-active" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={limit ?? undefined} aria-valuenow={used}>
        <div className={cn("h-full rounded-full", share >= 0.9 ? "bg-negative" : share >= 0.7 ? "bg-[var(--pill-warning-ink)]" : "bg-accent")} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

const SEGMENTS = [
  { key: "saved", label: "Saved", tone: "bg-positive" },
  { key: "review", label: "Needs a person", tone: "bg-[var(--pill-warning-ink)]" },
  { key: "failed", label: "Failed", tone: "bg-negative" },
  { key: "working", label: "Being read", tone: "bg-faint" },
] as const;

/** One column per day, stacked by where each document stands now. */
export function Throughput({ days }: { days: Overview["days"] }) {
  const max = Math.max(1, ...days.map((d) => d.received));
  const total = days.reduce((n, d) => n + d.received, 0);
  if (total === 0) return <p className="text-body text-muted">No documents were received in this period.</p>;
  return (
    <div>
      <div role="img" aria-label={`${total} documents received in the last ${days.length} days`} className={cn("flex h-40 items-end", days.length > 31 ? "gap-px" : "gap-1.5")}>
        {days.map((d) => {
          const label = new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
          return (
            <div
              key={d.date}
              className="group flex h-full min-w-0 flex-1 flex-col-reverse gap-px"
              title={`${label}: ${d.received} received (${d.saved} saved, ${d.review} need a person, ${d.failed} failed)`}
            >
              {SEGMENTS.map((s) => d[s.key] > 0 && (
                <span key={s.key} className={cn("block w-full first:rounded-b-[3px] last:rounded-t-[3px]", s.tone)} style={{ height: `${(d[s.key] / max) * 100}%` }} />
              ))}
              {d.received === 0 && <span className="block h-px w-full bg-border" />}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-meta text-muted">
        <span>{new Date(days[0].date + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
        <span>Busiest day: {max}</span>
        <span>Today</span>
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted">
        {SEGMENTS.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5"><span aria-hidden className={cn("size-2 rounded-full", s.tone)} />{s.label}</li>
        ))}
      </ul>
    </div>
  );
}
