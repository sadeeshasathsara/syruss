"use client";

import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/format";
import { IconButton } from "./Button";

/**
 * A panel that slides over the page: from the left for navigation on small screens,
 * from the right for the agent chat when there is no room to dock it.
 */
export function Sheet({
  open,
  onOpenChange,
  side,
  title,
  hideTitle,
  className,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  side: "left" | "right";
  title: string;
  hideTitle?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-[var(--scrim)]" />
        <D.Content
          aria-describedby={undefined}
          className={cn(
            "fixed inset-y-0 z-50 flex w-full flex-col bg-sheet shadow-float focus:outline-none",
            side === "left" ? "left-0 max-w-[300px]" : "right-0 max-w-[440px]",
            className,
          )}
        >
          <D.Title className={hideTitle ? "sr-only" : "px-4 pt-4 text-section font-semibold"}>{title}</D.Title>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

export function SheetClose({ label = "Close" }: { label?: string }) {
  return (
    <D.Close asChild>
      <IconButton aria-label={label}>
        <X />
      </IconButton>
    </D.Close>
  );
}

/** A centred dialog for confirmations. */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  size = "sm",
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  /** "sm" for a question, "lg" for a form. A tall dialog scrolls inside the screen. */
  size?: "sm" | "lg";
  children: React.ReactNode;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-[var(--scrim)]" />
        <D.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-card bg-sheet p-5 shadow-float focus:outline-none",
            size === "lg" ? "max-w-xl" : "max-w-md",
          )}
          {...(description ? {} : { "aria-describedby": undefined })}
        >
          <D.Title className="text-section font-semibold">{title}</D.Title>
          {description && <D.Description className="mt-1 text-body text-muted">{description}</D.Description>}
          <div className="mt-4">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
