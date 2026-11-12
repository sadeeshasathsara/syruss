"use client";

import { useQuery } from "@tanstack/react-query";
import { Search as SearchIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";

type Hit = { href: string; label: string; detail: string; kind: string };

/** Typing waits a moment before it asks, so each keystroke isn't a request. */
function useSettled(value: string, ms = 250): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/**
 * One box for everything: organizations and people by name, address or email; invoices by number; documents and runs by
 * id. Press / anywhere to jump to it; arrow keys and Enter pick a result.
 */
export function GlobalSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const settled = useSettled(q.trim());
  const found = useQuery({ queryKey: ["console", "search", settled], queryFn: () => api.console.search(settled), enabled: settled.length >= 2 });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, select, [contenteditable]");
      if (e.key === "/" && !typing) { e.preventDefault(); input.current?.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const r = found.data;
  const hits: Hit[] = r ? [
    ...r.organizations.map((o) => ({ href: `/accounts/organizations/${o.id}`, label: o.name, detail: `${o.slug}${o.status !== "active" ? ` · ${o.status}` : ""}`, kind: "Organization" })),
    ...r.people.map((p) => ({ href: `/accounts/people/${p.id}`, label: p.name, detail: `${p.email}${p.status !== "active" ? ` · ${p.status}` : ""}`, kind: "Person" })),
    ...r.invoices.map((i) => ({ href: `/billing/invoices/${i.id}`, label: i.number, detail: i.status, kind: "Invoice" })),
    ...r.documents.map((d) => ({ href: `/documents/${d.id}`, label: d.id.slice(0, 13), detail: d.status.toLowerCase(), kind: "Document" })),
    ...r.runs.map((x) => ({ href: `/runs/${x.id}`, label: x.id.slice(0, 13), detail: x.status.toLowerCase(), kind: "Agent run" })),
  ] : [];
  const go = (hit?: Hit) => {
    if (!hit) return;
    setOpen(false);
    setQ("");
    router.push(hit.href);
  };
  const showing = open && settled.length >= 2;

  return (
    <div className="relative w-full max-w-md">
      <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
      <input
        ref={input} value={q} role="combobox" aria-expanded={showing} aria-controls={`${id}-list`} aria-autocomplete="list"
        aria-label="Search organizations, people, invoices and ids" placeholder="Search…  ( / )"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setAt(0); }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setAt((a) => Math.min(hits.length - 1, a + 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setAt((a) => Math.max(0, a - 1)); }
          if (e.key === "Enter") { e.preventDefault(); go(hits[at]); }
          if (e.key === "Escape") { setOpen(false); input.current?.blur(); }
        }}
        className="h-8 w-full rounded-control border border-control bg-sheet pl-8 pr-2.5 text-body text-ink outline-none focus-visible:border-accent"
      />
      {showing && (
        <ul id={`${id}-list`} role="listbox" className="absolute left-0 right-0 top-9 z-30 max-h-96 overflow-y-auto rounded-card border border-border bg-sheet p-1 [box-shadow:var(--shadow-float)]">
          {found.isFetching && !r ? <li className="px-3 py-2 text-body text-muted">Looking…</li>
            : !hits.length ? <li className="px-3 py-2 text-body text-muted">Nothing matches “{settled}”.</li>
            : hits.map((h, i) => (
              <li key={h.href} role="option" aria-selected={i === at}>
                <button type="button" onMouseDown={(e) => { e.preventDefault(); go(h); }} onMouseEnter={() => setAt(i)}
                  className={cn("flex w-full items-baseline gap-2 rounded-control px-3 py-2 text-left", i === at ? "bg-active" : "hover:bg-raised")}>
                  <span className="w-24 flex-none text-meta text-muted">{h.kind}</span>
                  <span className="min-w-0 flex-1 truncate text-body text-ink">{h.label}</span>
                  <span className="max-w-[45%] truncate text-meta text-muted">{h.detail}</span>
                </button>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
