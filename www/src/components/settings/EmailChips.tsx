"use client";

import { X } from "lucide-react";
import { useRef } from "react";
import { cn } from "@/lib/format";

/** Characters that finish an address: a space, a comma, a semicolon or a new line. */
const SEPARATORS = /[\s,;]+/;

/** Splits typed or pasted text into cleaned-up addresses, in order, each once. */
export function splitAddresses(text: string, existing: string[] = []): string[] {
  const seen = new Set(existing);
  const out: string[] = [];
  for (const raw of text.split(SEPARATORS)) {
    const address = raw.trim().replace(/^<|>$/g, "").toLowerCase();
    if (!address || seen.has(address)) continue;
    seen.add(address);
    out.push(address);
  }
  return out;
}

/**
 * Email addresses as labels. Type an address and press space (or comma, semicolon, Enter) and it becomes a label;
 * press Backspace on an empty box and the last label turns back into text so you can fix it. Pasting a list makes a
 * label for each address. An address that isn't one is still labelled, in red, so it can be seen and removed.
 */
export function EmailChips({ id, chips, draft, onChange, isValid, placeholder, disabled }: {
  id: string;
  chips: string[];
  draft: string;
  onChange: (chips: string[], draft: string) => void;
  isValid: (address: string) => boolean;
  placeholder?: string;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);

  /** Labels whatever has been typed; what follows the last separator stays as text. */
  const commit = (text: string, keepTail: boolean) => {
    const parts = text.split(SEPARATORS);
    const tail = keepTail ? (parts.pop() ?? "") : "";
    const next = [...chips, ...splitAddresses(parts.join(" "), chips)];
    onChange(next, tail);
  };

  return (
    <div
      className={cn(
        "flex min-h-11 w-full flex-wrap items-center gap-1.5 rounded-control border border-control bg-sheet px-2 py-1.5 text-body text-ink",
        "focus-within:border-accent focus-within:ring-2 focus-within:ring-[var(--accent-soft)]",
        disabled && "opacity-60",
      )}
      onClick={() => input.current?.focus()}
    >
      {chips.map((address) => {
        const ok = isValid(address);
        return (
          <span
            key={address}
            className={cn(
              "inline-flex max-w-full items-center gap-1 rounded-chip py-0.5 pl-2 pr-1 text-meta font-medium",
              ok ? "bg-[var(--pill-neutral-bg)] text-[var(--pill-neutral-ink)]" : "bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]",
            )}
            title={ok ? undefined : "Not an email address"}
          >
            <span className="truncate">{address}</span>
            <button
              type="button"
              aria-label={`Remove ${address}`}
              disabled={disabled}
              className="grid size-4 flex-none place-items-center rounded-full hover:bg-black/10 dark:hover:bg-white/10"
              onClick={(e) => {
                e.stopPropagation();
                onChange(chips.filter((c) => c !== address), draft);
                input.current?.focus();
              }}
            >
              <X className="size-3" />
            </button>
          </span>
        );
      })}
      <input
        ref={input}
        id={id}
        type="text"
        inputMode="email"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        disabled={disabled}
        value={draft}
        placeholder={chips.length ? undefined : placeholder}
        className="h-7 min-w-[10rem] flex-1 bg-transparent px-1 text-body text-ink outline-none placeholder:text-faint"
        onChange={(e) => {
          const text = e.target.value;
          if (SEPARATORS.test(text)) commit(text, true);
          else onChange(chips, text);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && draft.trim()) {
            e.preventDefault();
            commit(draft, false);
          } else if (e.key === "Backspace" && draft === "" && chips.length) {
            // The last label becomes text again, ready to edit; the next Backspace deletes letters as usual.
            e.preventDefault();
            onChange(chips.slice(0, -1), chips[chips.length - 1]);
          }
        }}
        onBlur={() => {
          if (draft.trim()) commit(draft, false);
        }}
        onPaste={(e) => {
          const text = e.clipboardData.getData("text");
          if (SEPARATORS.test(text.trim())) {
            e.preventDefault();
            commit(draft + text, false);
          }
        }}
      />
    </div>
  );
}
