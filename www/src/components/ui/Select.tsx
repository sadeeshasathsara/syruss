"use client";

import * as S from "@radix-ui/react-select";
import { Check, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/format";

export interface SelectOption<T extends string> {
  value: T;
  label: string;
}

/**
 * The one dropdown: the same trigger and menu as the workspace switcher, keyboard and
 * screen-reader friendly. (An empty string can't be a value; use a word such as "all".)
 */
export function Select<T extends string>({
  value,
  onChange,
  options,
  id,
  label,
  className,
  disabled,
}: {
  value: T;
  onChange: (value: T) => void;
  options: SelectOption<T>[];
  id?: string;
  /** Accessible name when there is no visible <label for={id}>. */
  label?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <S.Root value={value} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
      <S.Trigger
        id={id}
        aria-label={label}
        className={cn(
          "flex h-9 w-full min-w-0 items-center gap-2 rounded-control border border-border bg-sheet px-2.5 text-left text-body text-ink",
          "transition-colors hover:border-control focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-60",
          "data-[state=open]:border-control pointer-coarse:h-11",
          className,
        )}
      >
        <span className="min-w-0 flex-1 truncate">
          <S.Value />
        </span>
        <S.Icon asChild>
          <ChevronsUpDown className="size-4 flex-none text-muted" />
        </S.Icon>
      </S.Trigger>
      <S.Portal>
        <S.Content
          position="popper"
          sideOffset={6}
          className="z-50 max-h-[min(320px,var(--radix-select-content-available-height))] min-w-[var(--radix-select-trigger-width)] animate-fade overflow-hidden rounded-card border border-border bg-sheet p-1 shadow-float"
        >
          <S.Viewport>
            {options.map((o) => (
              <S.Item
                key={o.value}
                value={o.value}
                className="flex h-8 cursor-pointer select-none items-center gap-2 rounded-control px-2 text-body text-ink outline-none data-[highlighted]:bg-raised data-[state=checked]:font-medium pointer-coarse:h-10"
              >
                <S.ItemText>{o.label}</S.ItemText>
                <S.ItemIndicator className="ml-auto">
                  <Check className="size-4 text-ink" />
                </S.ItemIndicator>
              </S.Item>
            ))}
          </S.Viewport>
        </S.Content>
      </S.Portal>
    </S.Root>
  );
}
