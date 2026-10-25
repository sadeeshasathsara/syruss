import { MousePointerClick } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/format";

/** The title row every screen starts with: one h1, an optional finding, actions on the right. */
export function PageHeader({
  title,
  subtitle,
  actions,
  back,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  back?: { href: string; label: string };
}) {
  return (
    <header className="flex flex-col gap-3 @3xl:flex-row @3xl:items-start @3xl:justify-between">
      <div className="min-w-0">
        {back && (
          <Link href={back.href} className="mb-1 inline-block text-body text-muted hover:text-ink">
            {back.label}
          </Link>
        )}
        <h1 className="line-clamp-2 text-title font-semibold tracking-[-0.01em] text-ink [overflow-wrap:anywhere]">{title}</h1>
        {subtitle && <p className="mt-1 text-body text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2 @3xl:flex-none @3xl:justify-end">{actions}</div>}
    </header>
  );
}

/** A bordered panel with an optional header row. */
export function Panel({
  title,
  aside,
  className,
  bodyClassName,
  children,
}: {
  title?: React.ReactNode;
  aside?: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("min-w-0 rounded-card bg-card [box-shadow:var(--shadow-card)]", className)}>
      {(title || aside) && (
        <div className="flex items-center justify-between gap-3 px-4 pt-3.5 pb-1">
          {title && <h2 className="text-section font-semibold text-ink">{title}</h2>}
          {aside && <div className="text-body text-muted">{aside}</div>}
        </div>
      )}
      <div className={cn("p-4", (title || aside) && "pt-2", bodyClassName)}>{children}</div>
    </section>
  );
}

/** An empty block keeps its frame and says what to do next. */
export function Empty({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-card border border-dashed border-control px-4 py-5">
      <p className="text-body font-medium text-ink">{title}</p>
      {children && <p className="max-w-prose text-body text-muted">{children}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** Loading: the final layout with flat bars at their real size, with a light passing over them. */
export function Bar({ className }: { className?: string }) {
  return <span className={cn("skeleton block rounded-chip", className)} aria-hidden />;
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-card bg-card [box-shadow:var(--shadow-card)] px-4 py-3 text-body">
      <span className="text-negative">{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="font-medium text-ink underline-offset-2 hover:underline">
          Retry
        </button>
      )}
    </div>
  );
}

/** Underline tabs on a full-width rule. Tabs scroll sideways on narrow screens, never the page. */
export function Tabs({ items }: { items: { href: string; label: string; count?: number; active: boolean }[] }) {
  return (
    <nav className="-mx-4 overflow-x-auto border-b border-border px-4 scrollbar-thin sm:mx-0 sm:px-0" aria-label="Views">
      <ul className="flex gap-5">
        {items.map((t) => (
          <li key={t.href} className="flex-none">
            <Link
              href={t.href}
              aria-current={t.active ? "page" : undefined}
              className={cn(
                "-mb-px inline-flex h-10 items-center gap-1.5 border-b-2 text-body",
                t.active ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink",
              )}
            >
              {t.label}
              {t.count !== undefined && <span className="num text-meta text-muted">{t.count}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** The detail column of a list page before anything is chosen: says to pick one from the list. */
export function PickOne({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="hidden min-h-80 flex-col items-center justify-center gap-1.5 rounded-card border border-dashed border-control px-6 py-10 text-center @4xl:flex">
      <MousePointerClick aria-hidden className="mb-1 size-6 text-muted" />
      <p className="text-body font-medium text-ink">{title}</p>
      {children && <p className="max-w-sm text-body text-muted">{children}</p>}
    </div>
  );
}
