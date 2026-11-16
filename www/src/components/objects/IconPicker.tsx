"use client";

import * as D from "@radix-ui/react-dialog";
import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import { IconButton } from "@/components/ui/Button";
import { cn } from "@/lib/format";
import { OBJECT_ICON_GROUPS, OBJECT_ICON_HINTS, OBJECT_ICONS } from "@/lib/objectIcons";
import { ObjectIcon } from "./ObjectIcon";

/**
 * The object's icon as a button; pressing it opens the icon list, searchable by name and by what each icon suits
 * ("vehicle", "money"). The agent picks one when it designs the template; anyone editing it can change it.
 */
export function IconPicker({ value, objectName, onChange }: { value?: string; objectName: string; onChange: (icon: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const fits = (name: string) => words.every((w) => name.includes(w) || (OBJECT_ICON_HINTS[name] ?? "").toLowerCase().includes(w));
    return OBJECT_ICON_GROUPS.map((g) => ({ ...g, icons: g.icons.filter(fits) })).filter((g) => g.icons.length);
  }, [q]);

  return (
    <D.Root open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQ(""); }}>
      <D.Trigger asChild>
        <button type="button" title="Change the icon" aria-label={`Icon of ${objectName}: ${value ?? "none"}. Change it`}
          className="rounded-control outline-offset-2 hover:opacity-80">
          <ObjectIcon name={value} />
        </button>
      </D.Trigger>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-[var(--scrim)]" />
        <D.Content aria-describedby={undefined}
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[min(640px,90vh)] w-[min(560px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-sheet bg-sheet shadow-float focus:outline-none">
          <div className="flex items-center gap-3 border-b border-border px-4 py-3">
            <D.Title className="min-w-0 flex-1 truncate text-section font-semibold text-ink">An icon for {objectName}</D.Title>
            <D.Close asChild><IconButton aria-label="Close"><X /></IconButton></D.Close>
          </div>
          <label className="mx-4 mt-3 flex h-9 items-center gap-2 rounded-control border border-control bg-card px-2.5 focus-within:border-ink">
            <Search className="size-4 flex-none text-muted" aria-hidden />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search: a person, a place, money, a vehicle…"
              aria-label="Search icons" className="min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-faint" />
          </label>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-2 scrollbar-thin">
            {groups.length === 0 && <p className="py-6 text-center text-body text-muted">No icon fits “{q}”. Try another word.</p>}
            {groups.map((g) => (
              <section key={g.label} className="mt-3">
                <h3 className="mb-1.5 text-meta font-semibold uppercase tracking-wide text-faint">{g.label}</h3>
                <ul className="grid grid-cols-[repeat(auto-fill,minmax(44px,1fr))] gap-1">
                  {g.icons.map((name) => {
                    const Icon = OBJECT_ICONS[name];
                    return (
                      <li key={name}>
                        <button type="button" title={`${name}: ${OBJECT_ICON_HINTS[name] ?? ""}`} aria-label={`${name}, ${OBJECT_ICON_HINTS[name] ?? ""}`}
                          aria-pressed={value === name}
                          onClick={() => { onChange(name); setOpen(false); setQ(""); }}
                          className={cn("flex aspect-square w-full items-center justify-center rounded-control text-muted hover:bg-raised hover:text-ink",
                            value === name && "bg-[var(--pill-accent-bg)] text-[var(--pill-accent-ink)] ring-1 ring-[var(--pill-accent-ink)]")}>
                          <Icon className="size-5" strokeWidth={1.75} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
