"use client";

import * as T from "@radix-ui/react-tooltip";

export const TooltipProvider = T.Provider;

export function Tooltip({ label, children, side = "top" }: { label: string; children: React.ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          className="z-50 rounded-chip bg-primary px-2 py-1 text-meta text-primary-ink shadow-float"
        >
          {label}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
