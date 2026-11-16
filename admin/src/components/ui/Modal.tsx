"use client";

import { X } from "lucide-react";
import { createContext, useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/format";

/** The open dialog's element: menus opened inside it (Select) are placed in it, or they would sit behind it. */
export const ModalContainer = createContext<HTMLElement | null>(null);

/**
 * A popup: a native <dialog> opened as a modal, so focus stays inside it, Escape closes it, and the page behind can't be reached.
 * Clicking the dimmed area outside it closes it too.
 */
export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [element, setElement] = useState<HTMLElement | null>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={(node) => { ref.current = node; if (node !== element) setElement(node); }}
      aria-label={title}
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      className={cn("m-auto w-[calc(100%-2rem)] rounded-card border border-border bg-sheet p-0 text-ink shadow-float backdrop:bg-black/50",
        wide ? "max-w-3xl" : "max-w-xl")}
    >
      {open && (
        <ModalContainer.Provider value={element}>
          <div className="flex items-center justify-between gap-4 border-b border-rule px-5 py-3">
            <h2 className="text-body font-semibold">{title}</h2>
            <button type="button" aria-label="Close" onClick={onClose} className="flex size-8 items-center justify-center rounded text-muted hover:bg-well hover:text-ink">
              <X aria-hidden className="size-4" />
            </button>
          </div>
          <div className="max-h-[75vh] overflow-y-auto p-5">{children}</div>
        </ModalContainer.Provider>
      )}
    </dialog>
  );
}
