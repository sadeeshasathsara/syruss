"use client";

import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronsUpDown, Search } from "lucide-react";
import { Children, Fragment, isValidElement, useId, useRef, useState, type ReactElement, type ReactNode } from "react";
import { cn } from "@/lib/format";

interface Option {
  value: string;
  label: string;
  disabled?: boolean;
}

/** Lists longer than this get a search box. */
const SEARCH_FROM = 7;

/** The <option>s given as children (also inside fragments, arrays and `cond && <option>`), as plain data. */
function optionsOf(children: ReactNode): Option[] {
  const out: Option[] = [];
  const walk = (nodes: ReactNode) => {
    Children.forEach(nodes, (child) => {
      if (!isValidElement(child)) return;
      const el = child as ReactElement<{ value?: string; disabled?: boolean; children?: ReactNode }>;
      if (el.type === Fragment) walk(el.props.children);
      else if (el.type === "option") {
        const label = Children.toArray(el.props.children).join("");
        out.push({ value: String(el.props.value ?? label), label, disabled: el.props.disabled });
      }
    });
  };
  walk(children);
  return out;
}

/**
 * The dropdown for the whole admin app: the same trigger and menu as the customer app's, in place of the browser's own. It is
 * written like a <select> (children are <option>s) but reports the chosen value itself. A long list gets a search box that filters
 * as you type; arrow keys, Enter and Escape work throughout.
 */
export function Select({
  value,
  onValueChange,
  children,
  className,
  disabled,
  searchable,
  "aria-label": label,
  id,
}: {
  value: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  /** Force the search box on or off (default: on for long lists). */
  searchable?: boolean;
  "aria-label"?: string;
  id?: string;
}) {
  const options = optionsOf(children);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();
  const withSearch = searchable ?? options.length > SEARCH_FROM;
  const q = query.trim().toLowerCase();
  const shown = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  const current = options.find((o) => o.value === value);

  const choose = (o: Option) => {
    if (o.disabled) return;
    setOpen(false);
    if (o.value !== value) onValueChange(o.value);
  };
  const move = (by: number) => {
    if (!shown.length) return;
    let i = active;
    for (let n = 0; n < shown.length; n++) {
      i = (i + by + shown.length) % shown.length;
      if (!shown[i].disabled) break;
    }
    setActive(i);
    list.current?.children[i]?.scrollIntoView({ block: "nearest" });
  };
  const onKeys = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") { e.preventDefault(); if (shown[active]) choose(shown[active]); }
  };

  return (
    <Popover.Root open={open} onOpenChange={(o) => {
      setOpen(o);
      if (o) { setQuery(""); setActive(Math.max(0, options.findIndex((x) => x.value === value))); }
    }}>
      <Popover.Trigger
        id={id}
        type="button"
        aria-label={label}
        aria-haspopup="listbox"
        disabled={disabled}
        className={cn(
          "inline-flex h-8 min-w-36 max-w-full items-center gap-2 rounded-control border border-control bg-sheet px-2.5 text-left text-body text-ink outline-none",
          "transition-colors hover:border-faint focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-60 data-[state=open]:border-faint",
          className,
        )}
      >
        <span className={cn("min-w-0 flex-1 truncate", !current && "text-muted")}>{current?.label ?? "Choose…"}</span>
        <ChevronsUpDown aria-hidden className="size-4 flex-none text-muted" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={12}
          onKeyDown={onKeys}
          onOpenAutoFocus={(e) => { if (!withSearch) { e.preventDefault(); (e.currentTarget as HTMLElement).focus(); } }}
          className="z-50 w-max min-w-[var(--radix-popover-trigger-width)] max-w-[min(28rem,var(--radix-popover-content-available-width))] animate-fade rounded-card border border-border bg-sheet p-1 shadow-float outline-none"
        >
          {withSearch && (
            <div className="relative mb-1">
              <Search aria-hidden className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <input
                autoFocus
                value={query}
                onChange={(e) => { setQuery(e.target.value); setActive(0); }}
                placeholder="Search…"
                aria-label="Search the list"
                aria-controls={listId}
                className="h-8 w-full rounded-control border border-control bg-sheet pl-8 pr-2 text-body text-ink outline-none focus-visible:border-accent"
              />
            </div>
          )}
          <ul id={listId} ref={list} role="listbox" className="max-h-[min(18rem,var(--radix-popover-content-available-height))] overflow-y-auto">
            {shown.map((o, i) => (
              <li
                key={o.value}
                role="option"
                aria-selected={o.value === value}
                aria-disabled={o.disabled}
                onMouseMove={() => setActive(i)}
                onClick={() => choose(o)}
                className={cn(
                  "flex h-8 cursor-pointer select-none items-center gap-2 rounded-control px-2 text-body text-ink",
                  i === active && "bg-raised", o.value === value && "font-medium", o.disabled && "cursor-not-allowed opacity-50",
                )}
              >
                <span className="min-w-0 flex-1 truncate">{o.label}</span>
                {o.value === value && <Check aria-hidden className="size-4 flex-none" />}
              </li>
            ))}
            {!shown.length && <li className="px-2 py-2 text-meta text-muted">Nothing matches “{query}”.</li>}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
