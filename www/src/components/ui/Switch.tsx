"use client";

import { cn } from "@/lib/format";

/** An on/off setting that applies as soon as it's flipped. */
export function Switch({ checked, onChange, label, disabled, id }: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 flex-none items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-control",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block size-4 rounded-full bg-sheet shadow-sm transition-transform duration-150",
          checked ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
    </button>
  );
}
