"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { Bar } from "@/components/ui/Blocks";
import { IconButton } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { ago, cn } from "@/lib/format";
import type { FieldFigure, Paged, ProfileDocument, ThingSummary, WeekCount } from "@/lib/types";
import { ObjectIcon } from "./ObjectIcon";

// The pieces of the object and thing profiles (a copy of the customer app's www/src/components/objects/Profile.tsx).

export function humanize(key: string): string {
  const s = key.replace(/_/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function display(v: unknown): string {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

const STATUS_TONE: Record<string, "positive" | "warning" | "negative" | "neutral"> = {
  COMPLETED: "positive", NEEDS_REVIEW: "warning", AWAITING_INPUT: "warning", FAILED: "negative",
};

export const TYPE_LABEL: Record<string, string> = {
  string: "Text", number: "Number", integer: "Whole number", date: "Date", time: "Time", boolean: "Yes / no",
};

/** The profile's header: the icon as its picture, the name, what it is, and the facts that place it. */
export function ProfileHeader({ icon, title, kind, description, facts, back, actions }: {
  icon: string; title: React.ReactNode; kind: React.ReactNode; description?: string; facts?: React.ReactNode;
  back?: { href: string; label: string }; actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3">
      {back && <Link href={back.href} className="self-start text-body text-muted hover:text-ink">{back.label}</Link>}
      <div className="flex flex-col gap-4 @2xl:flex-row @2xl:items-center">
        <ObjectIcon name={icon} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="text-meta font-semibold uppercase tracking-wide text-faint">{kind}</p>
          <h1 className="line-clamp-2 text-title font-semibold tracking-[-0.01em] text-ink [overflow-wrap:anywhere]">{title}</h1>
          {description && <p className="mt-1 max-w-[70ch] text-body text-muted">{description}</p>}
          {facts && <div className="mt-2 flex flex-wrap items-center gap-2">{facts}</div>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
    </header>
  );
}

/** A strip of figures in one container split by rules. */
export function Stats({ items }: { items: { label: string; value: React.ReactNode }[] }) {
  return (
    <section aria-label="In figures" className="grid grid-cols-2 overflow-hidden rounded-card bg-card [box-shadow:var(--shadow-card)] @2xl:grid-cols-4">
      {items.map((s, i) => (
        <div key={s.label} className={cn("flex min-w-0 flex-col gap-1 border-border px-4 py-3",
          i % 2 === 1 && "border-l", i >= 2 && "border-t @2xl:border-t-0", i > 0 && "@2xl:border-l")}>
          <span className="truncate text-body text-muted">{s.label}</span>
          <span className="num truncate text-figure text-ink">{s.value}</span>
        </div>
      ))}
    </section>
  );
}

/** Documents per week, oldest first. */
export function WeekBars({ weeks, what }: { weeks: WeekCount[]; what: string }) {
  const max = Math.max(1, ...weeks.map((w) => w.count));
  const total = weeks.reduce((n, w) => n + w.count, 0);
  if (!total) return <p className="text-body text-muted">No {what} in the last {weeks.length} weeks.</p>;
  const day = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <div>
      <div role="img" aria-label={`${total} ${what} in the last ${weeks.length} weeks`} className="flex h-28 items-end gap-1.5">
        {weeks.map((w) => (
          <div key={w.week} className="flex h-full min-w-0 flex-1 flex-col justify-end" title={`Week of ${day(w.week)}: ${w.count}`}>
            {w.count > 0 ? <span className="block w-full rounded-t-[3px] bg-accent" style={{ height: `${(w.count / max) * 100}%` }} />
              : <span className="block h-px w-full bg-border" />}
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-meta text-muted">
        <span>Week of {day(weeks[0].week)}</span><span>Busiest week: {max}</span><span>This week</span>
      </div>
    </div>
  );
}

const num = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 });

/** Per attribute: how often it is filled, and its totals or commonest values. */
export function Figures({ figures }: { figures: FieldFigure[] }) {
  if (!figures.length) return <p className="text-body text-muted">This object has no attributes.</p>;
  return (
    <ul className="flex flex-col">
      {figures.map((f) => {
        const share = f.rows ? f.filled / f.rows : 0;
        return (
          <li key={f.field} className="grid grid-cols-1 gap-x-6 gap-y-1.5 border-t border-rule py-2.5 first:border-0 @3xl:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
            <div className="min-w-0">
              <div className="flex items-baseline justify-between gap-2 text-body">
                <span className="truncate text-ink">{humanize(f.field)}</span>
                <span className="num flex-none text-meta text-muted">{f.rows ? `${Math.round(share * 100)}% filled` : "—"}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-active" role="meter" aria-label={`${humanize(f.field)} filled`}
                aria-valuemin={0} aria-valuemax={f.rows} aria-valuenow={f.filled}>
                <div className="h-full rounded-full bg-accent" style={{ width: `${share * 100}%` }} />
              </div>
            </div>
            <div className="min-w-0 text-body text-muted">
              {f.number ? (
                <span className="num">Total <b className="font-medium text-ink">{num(f.number.sum)}</b> · average {num(f.number.average)} · from {num(f.number.min)} to {num(f.number.max)}</span>
              ) : f.range ? (
                <span className="num">From <b className="font-medium text-ink">{f.range.first}</b> to <b className="font-medium text-ink">{f.range.last}</b></span>
              ) : f.top?.length ? (
                <span className="flex flex-wrap gap-1.5">
                  {f.top.map((t) => (
                    <span key={t.value} className="inline-flex max-w-full items-center gap-1 rounded-chip bg-raised px-1.5 text-meta">
                      <span className="truncate text-ink">{t.value}</span><span className="num">×{t.count}</span>
                    </span>
                  ))}
                  {f.distinct != null && f.distinct > f.top.length && <span className="text-meta">and {f.distinct - f.top.length} other values</span>}
                </span>
              ) : f.distinct ? `${f.distinct} different value${f.distinct === 1 ? "" : "s"}` : f.filled ? "" : "Never filled in"}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Pager({ page, onPage }: { page: Paged<unknown>; onPage: (p: number) => void }) {
  const pages = Math.ceil(page.total / page.page_size);
  if (pages <= 1) return null;
  return (
    <div className="mt-2 flex items-center justify-end gap-1 text-meta text-muted">
      <IconButton aria-label="Newer" disabled={page.page <= 0} onClick={() => onPage(page.page - 1)}><ChevronLeft /></IconButton>
      <span className="num">Page {page.page + 1} of {pages}</span>
      <IconButton aria-label="Older" disabled={page.page >= pages - 1} onClick={() => onPage(page.page + 1)}><ChevronRight /></IconButton>
    </div>
  );
}

/** The documents that have this object (or mention this thing), newest first. */
export function DocumentList({ page, href, onPage, rowsLabel }: {
  page?: Paged<ProfileDocument>; href: (id: string) => string; onPage: (p: number) => void; rowsLabel?: (n: number) => string;
}) {
  if (!page) return <div className="flex flex-col gap-2">{[0, 1, 2].map((i) => <Bar key={i} className="h-9 w-full" />)}</div>;
  if (!page.items.length) return <p className="text-body text-muted">No saved document has it yet.</p>;
  return (
    <div>
      <ul className="flex flex-col">
        {page.items.map((d) => (
          <li key={d.id} className="border-t border-rule first:border-0">
            <Link href={href(d.id)} className="flex min-w-0 items-center gap-3 py-2.5 hover:bg-raised">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body text-ink">{d.name}</span>
                <span className="block truncate text-meta text-muted">{d.name !== d.file_name ? `${d.file_name} · ` : ""}received {ago(d.received_at)}</span>
              </span>
              {rowsLabel && d.rows > 1 && <span className="num flex-none text-meta text-muted">{rowsLabel(d.rows)}</span>}
              <Pill tone={STATUS_TONE[d.status] ?? "neutral"}>{humanize(d.status.toLowerCase())}</Pill>
            </Link>
          </li>
        ))}
      </ul>
      <Pager page={page} onPage={onPage} />
    </div>
  );
}

/** Things of one object, most mentioned first. */
export function ThingList({ things, href, icon }: { things: ThingSummary[]; href: (t: ThingSummary) => string; icon: string }) {
  if (!things.length) return <p className="text-body text-muted">None met yet: they appear here once documents that name them are saved.</p>;
  return (
    <ul className="flex flex-col">
      {things.map((t) => (
        <li key={t.key} className="border-t border-rule first:border-0">
          <Link href={href(t)} className="flex min-w-0 items-center gap-3 py-2 hover:bg-raised">
            <ObjectIcon name={icon} size="sm" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body text-ink">{t.label}</span>
              {t.label !== t.id && <span className="block truncate text-meta text-muted">{t.id}</span>}
            </span>
            <span className="num flex-none text-meta text-muted">{t.documents} document{t.documents === 1 ? "" : "s"} · {ago(t.last_seen)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** An attribute's value as people read it. */
export function value(v: unknown): string {
  return v === null || v === undefined || v === "" ? "—" : display(v);
}
