"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ArrowRight, Bot, Building2, CornerDownLeft, FileText, History, LayoutGrid, LifeBuoy, Loader2, MessageSquareText, Receipt,
  Search as SearchIcon, ShieldCheck, UserRound, X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type ComponentType,
} from "react";
import { cn } from "@/lib/format";

/*
 * The command palette: one search for everything, opened from the top bar, with Ctrl+K (⌘K on a Mac) or "/".
 * Pages to go to are matched as you type; anything else is asked of the server, which answers in groups. Arrow keys
 * move, Enter opens (Ctrl/⌘+Enter in a new tab), Esc closes. Recent searches and what you opened are kept in this
 * browser only, and the palette works without them.
 */

/** One thing the server found. `href` is where it opens. */
export interface SearchItem {
  id: string;
  title: string;
  detail: string;
  href: string;
  status?: string | null;
  [extra: string]: unknown;
}

export interface SearchAnswer {
  query: string;
  groups: { key: string; label: string; items: SearchItem[] }[];
}

/** A page of the app to go to, matched by its label and keywords. */
export interface Place {
  label: string;
  href: string;
  /** Where it sits, shown beside it ("Billing") */
  within?: string;
  keywords?: string;
}

export interface PaletteConfig {
  search: (q: string) => Promise<SearchAnswer>;
  /** Kept apart per app and per organization */
  scope: string;
  places: Place[];
  placeholder: string;
  /** Turns a found item's href into an address in this app (the customer app puts the organization in front) */
  hrefFor?: (item: SearchItem, group: string) => string;
  /** Opens an item some other way than going to its address; returns true when it did */
  open?: (item: SearchItem, group: string) => boolean;
}

/** An item as the palette lists it: what was found, or a place, or something opened before. */
interface Row {
  key: string;
  group: string;
  title: string;
  detail?: string;
  status?: string | null;
  href: string;
  item?: SearchItem;
  /** A recent search: picking it searches again */
  query?: string;
}

const ICONS: Record<string, ComponentType<{ className?: string }>> = {
  organizations: Building2, people: UserRound, documents: FileText, runs: Bot, tickets: LifeBuoy, invoices: Receipt,
  workspaces: LayoutGrid, staff: ShieldCheck, chats: MessageSquareText, places: ArrowRight, recent: History, searches: SearchIcon,
};

interface Section {
  key: string;
  label: string;
  rows: Row[];
  action?: { label: string; run: () => void };
}

// ---- this browser's memory ------------------------------------------------------------------------------------------

const MAX_SEARCHES = 5;
const MAX_OPENED = 6;

function load<T>(key: string): T[] {
  try {
    const v = JSON.parse(localStorage.getItem(key) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

function save(key: string, value: unknown[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: nothing is remembered */
  }
}

// ---- matching -------------------------------------------------------------------------------------------------------

const words = (q: string) => q.toLowerCase().split(/\s+/).filter(Boolean);

function placeScore(p: Place, q: string): number | null {
  const label = p.label.toLowerCase();
  const all = `${label} ${(p.within ?? "").toLowerCase()} ${(p.keywords ?? "").toLowerCase()}`;
  const ws = words(q);
  if (!ws.length || !ws.every((w) => all.includes(w))) return null;
  const whole = q.trim().toLowerCase();
  return label === whole ? 0 : label.startsWith(whole) ? 1 : label.includes(whole) ? 2 : 3;
}

/** The text with each typed word marked where it appears. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const ws = words(query).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!ws.length || !text) return <>{text}</>;
  const parts = text.split(new RegExp(`(${ws.join("|")})`, "gi"));
  return (
    <>
      {parts.map((part, i) => (i % 2 === 1
        ? <mark key={i} className="rounded-[2px] bg-[var(--pill-accent-bg)] text-[var(--pill-accent-ink)]">{part}</mark>
        : part))}
    </>
  );
}

function useSettled(value: string, ms = 180): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/** "⌘K" on a Mac, "Ctrl K" elsewhere ("Ctrl K" while rendering on the server). */
function useShortcutLabel(): string {
  return useSyncExternalStore(
    () => () => {},
    () => (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? "⌘K" : "Ctrl K"),
    () => "Ctrl K",
  );
}

// ---- the palette ----------------------------------------------------------------------------------------------------

const PaletteContext = createContext<{ show: () => void } | null>(null);

/** Holds the palette for the frame: the keyboard shortcuts open it from anywhere, and any trigger inside opens it. */
export function PaletteProvider({ config, children }: { config: PaletteConfig; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const show = useCallback(() => setOpen(true), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])");
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const value = useMemo(() => ({ show }), [show]);
  return (
    <PaletteContext.Provider value={value}>
      {children}
      {open && <Palette config={config} onClose={() => setOpen(false)} />}
    </PaletteContext.Provider>
  );
}

/** What opens the palette: a search field in a wide top bar, or an icon in a narrow one. */
export function SearchTrigger({ variant = "bar", className, label = "Search" }: { variant?: "bar" | "icon"; className?: string; label?: string }) {
  const ctx = useContext(PaletteContext);
  const shortcut = useShortcutLabel();
  if (!ctx) return null;
  if (variant === "icon") {
    return (
      <button type="button" onClick={ctx.show} aria-label={label} aria-haspopup="dialog"
        className={cn("inline-flex size-8 flex-none items-center justify-center rounded-control text-muted hover:bg-raised hover:text-ink pointer-coarse:size-11", className)}>
        <SearchIcon className="size-4" aria-hidden />
      </button>
    );
  }
  return (
    <button type="button" onClick={ctx.show} aria-haspopup="dialog" aria-keyshortcuts="Control+K Meta+K /"
      className={cn("flex h-8 w-full max-w-md items-center gap-2 rounded-control border border-control bg-sheet px-2.5 text-left text-body text-muted transition-colors hover:border-accent/60 hover:text-ink", className)}>
      <SearchIcon className="size-4 flex-none" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{label}…</span>
      <kbd className="num hidden flex-none rounded-[4px] border border-border bg-raised px-1.5 text-meta text-muted sm:inline">{shortcut}</kbd>
    </button>
  );
}

function Palette({ config, onClose }: { config: PaletteConfig; onClose: () => void }) {
  const router = useRouter();
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const searchesKey = `palette:${config.scope}:searches`;
  const openedKey = `palette:${config.scope}:opened`;
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [searches, setSearches] = useState<string[]>(() => load<string>(searchesKey));
  const [opened, setOpened] = useState<Row[]>(() => load<Row>(openedKey));
  const text = q.trim();
  const settled = useSettled(text);
  const asking = settled.length >= 2;
  const found = useQuery({
    queryKey: ["palette", config.scope, settled],
    queryFn: () => config.search(settled),
    enabled: asking,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

  // Back to whatever had focus before; the page behind doesn't scroll while the palette is open.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.documentElement.style.overflow = overflow;
      before?.focus?.();
    };
  }, []);

  const sections = useMemo((): Section[] => {
    const out: Section[] = [];
    if (!text) {
      if (searches.length) {
        out.push({ key: "searches", label: "Recent searches", rows: searches.map((s) => ({ key: `s:${s}`, group: "searches", title: s, href: "", query: s })),
          action: { label: "Clear", run: () => { setSearches([]); save(searchesKey, []); } } });
      }
      if (opened.length) {
        out.push({ key: "recent", label: "Opened recently", rows: opened.map((r) => ({ ...r, key: `r:${r.key}` })),
          action: { label: "Clear", run: () => { setOpened([]); save(openedKey, []); } } });
      }
      out.push({ key: "places", label: "Go to", rows: config.places.slice(0, 8).map((p) => ({ key: `p:${p.href}`, group: "places", title: p.label, detail: p.within, href: p.href })) });
      return out;
    }
    const places = config.places.map((p) => ({ p, score: placeScore(p, text) })).filter((x) => x.score !== null)
      .sort((a, b) => a.score! - b.score!).slice(0, asking ? 4 : 8)
      .map(({ p }): Row => ({ key: `p:${p.href}`, group: "places", title: p.label, detail: p.within, href: p.href }));
    // Only results for what is typed now (or the last answer while the next is on its way)
    const groups = asking && found.data ? found.data.groups : [];
    const exact = places.length && groups.some((g) => g.items.some((i) => i.title.toLowerCase() === text.toLowerCase()));
    const server = groups.map((g): Section => ({
      key: g.key, label: g.label,
      rows: g.items.map((i) => ({ key: `${g.key}:${i.id}`, group: g.key, title: i.title, detail: i.detail, status: i.status,
                                  href: config.hrefFor ? config.hrefFor(i, g.key) : i.href, item: i })),
    }));
    if (places.length) {
      const go = { key: "places", label: "Go to", rows: places };
      return exact ? [...server, go] : [go, ...server];
    }
    return server;
  }, [text, asking, searches, opened, config, found.data, searchesKey, openedKey]);

  const rows = sections.flatMap((s) => s.rows);
  const starts = sections.map((_, n) => sections.slice(0, n).reduce((sum, x) => sum + x.rows.length, 0));
  const at = rows.length ? Math.min(active, rows.length - 1) : -1;
  const optionId = (i: number) => `${id}-opt-${i}`;

  useEffect(() => {
    if (at >= 0) document.getElementById(optionId(at))?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [at]);

  const remember = (row: Row) => {
    if (text.length >= 2) {
      const next = [text, ...searches.filter((s) => s.toLowerCase() !== text.toLowerCase())].slice(0, MAX_SEARCHES);
      save(searchesKey, next);
    }
    if (row.group !== "places" && row.group !== "searches") {
      const kept: Row = { key: row.key.replace(/^r:/, ""), group: row.group, title: row.title, detail: row.detail, status: row.status, href: row.href, item: row.item };
      save(openedKey, [kept, ...opened.filter((o) => o.key !== kept.key)].slice(0, MAX_OPENED));
    }
  };

  const pick = (row: Row | undefined, newTab = false) => {
    if (!row) return;
    if (row.query !== undefined) {
      setQ(row.query);
      setActive(0);
      input.current?.focus();
      return;
    }
    remember(row);
    if (newTab) {
      window.open(row.href, "_blank", "noopener");
      return;
    }
    onClose();
    if (row.item && config.open?.(row.item, row.group)) return;
    router.push(row.href);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const n = rows.length;
    if (e.key === "ArrowDown" || (e.key === "n" && e.ctrlKey)) { e.preventDefault(); if (n) setActive((at + 1) % n); }
    else if (e.key === "ArrowUp" || (e.key === "p" && e.ctrlKey)) { e.preventDefault(); if (n) setActive((at - 1 + n) % n); }
    else if (e.key === "Home" && e.ctrlKey) { e.preventDefault(); setActive(0); }
    else if (e.key === "End" && e.ctrlKey) { e.preventDefault(); setActive(Math.max(0, n - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(rows[at], e.metaKey || e.ctrlKey); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
    else if (e.key === "Tab") { e.preventDefault(); input.current?.focus(); }
  };

  const waiting = asking && (found.isFetching && (!found.data || settled !== text || found.isPlaceholderData));
  const failed = asking && found.isError;
  const nothing = asking && !waiting && !failed && found.data && rows.length === 0;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center sm:px-4 sm:pt-[12vh]" onKeyDown={onKeyDown}>
      <button type="button" aria-label="Close search" tabIndex={-1} className="absolute inset-0 bg-black/40 animate-fade" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Search"
        className="relative flex h-dvh w-full flex-col overflow-hidden bg-sheet [box-shadow:var(--shadow-float)] sm:h-auto sm:max-h-[min(640px,76vh)] sm:max-w-xl sm:rounded-card sm:border sm:border-border">
        <div className="flex flex-none items-center gap-2 border-b border-border px-3">
          {waiting ? <Loader2 className="size-4 flex-none animate-spin text-muted" aria-hidden /> : <SearchIcon className="size-4 flex-none text-muted" aria-hidden />}
          <input
            ref={input} autoFocus value={q} placeholder={config.placeholder} spellCheck={false} autoComplete="off"
            role="combobox" aria-expanded={rows.length > 0} aria-controls={`${id}-list`} aria-autocomplete="list"
            aria-activedescendant={at >= 0 ? optionId(at) : undefined} aria-label="Search"
            onChange={(e) => { setQ(e.target.value); setActive(0); }}
            className="h-12 min-w-0 flex-1 bg-transparent text-[16px] text-ink outline-none placeholder:text-faint sm:text-body"
          />
          {q && (
            <button type="button" onClick={() => { setQ(""); setActive(0); input.current?.focus(); }} aria-label="Clear the search"
              className="inline-flex size-7 items-center justify-center rounded-control text-muted hover:bg-raised hover:text-ink">
              <X className="size-4" aria-hidden />
            </button>
          )}
          <button type="button" onClick={onClose} className="rounded-control px-2 py-1 text-meta text-muted hover:bg-raised hover:text-ink sm:hidden">Cancel</button>
          <kbd className="hidden rounded-[4px] border border-border bg-raised px-1.5 text-meta text-muted sm:inline">Esc</kbd>
        </div>

        <div id={`${id}-list`} role="listbox" aria-label="Results" className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5 scrollbar-thin">
          {sections.map((s, n) => (
            <div key={s.key} role="group" aria-labelledby={`${id}-${s.key}`} className="pb-1.5">
              <div className="flex items-center px-2 pb-1 pt-2">
                <p id={`${id}-${s.key}`} className="flex-1 text-meta font-semibold uppercase tracking-wide text-faint">{s.label}</p>
                {s.action && (
                  <button type="button" onClick={s.action.run} className="text-meta text-muted hover:text-ink">{s.action.label}</button>
                )}
              </div>
              {s.rows.map((row, k) => {
                const i = starts[n] + k;
                const Icon = ICONS[row.group] ?? ArrowRight;
                return (
                  <div key={row.key} id={optionId(i)} role="option" aria-selected={i === at}
                    onMouseMove={() => { if (i !== at) setActive(i); }}
                    onClick={(e) => pick(row, e.metaKey || e.ctrlKey)}
                    className={cn("group flex cursor-pointer items-center gap-2.5 rounded-control px-2 py-2 pointer-coarse:py-2.5", i === at ? "bg-active" : "hover:bg-raised")}>
                    <Icon className="size-4 flex-none text-muted" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body text-ink"><Highlight text={row.title} query={text} /></span>
                      {row.detail && <span className="block truncate text-meta text-muted"><Highlight text={row.detail} query={text} /></span>}
                    </span>
                    {row.status && <span className="flex-none rounded-chip bg-raised px-1.5 text-meta text-muted">{row.status.replace(/_/g, " ")}</span>}
                    {i === at && <CornerDownLeft className="hidden size-3.5 flex-none text-faint sm:block" aria-hidden />}
                  </div>
                );
              })}
            </div>
          ))}
          {text.length === 1 && !rows.length && <p className="px-3 py-6 text-center text-body text-muted">Keep typing to search.</p>}
          {waiting && !rows.length && <p className="px-3 py-6 text-center text-body text-muted" role="status">Searching…</p>}
          {failed && <p className="px-3 py-6 text-center text-body text-negative" role="alert">{(found.error as Error).message || "The search didn't answer. Try again."}</p>}
          {nothing && (
            <div className="px-3 py-8 text-center" role="status">
              <p className="text-body text-ink">Nothing matches “{text}”</p>
              <p className="mt-1 text-meta text-muted">Try fewer words, part of a name, or a full id.</p>
            </div>
          )}
        </div>

        <div className="hidden flex-none items-center gap-4 border-t border-border px-3 py-2 text-meta text-faint sm:flex">
          <span><kbd className="font-sans">↑</kbd> <kbd className="font-sans">↓</kbd> to move</span>
          <span><kbd className="font-sans">Enter</kbd> to open</span>
          <span><kbd className="font-sans">Ctrl/⌘ Enter</kbd> in a new tab</span>
          <span className="ml-auto"><kbd className="font-sans">Esc</kbd> to close</span>
        </div>
      </div>
    </div>
  );
}
