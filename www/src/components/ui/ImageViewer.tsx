"use client";

import * as D from "@radix-ui/react-dialog";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useRef } from "react";
import { IconButton } from "./Button";
import { ZoomableImage } from "./ZoomableImage";

export interface ViewerImage {
  src: string;
  title: string;
}

/**
 * Pages at full size, one at a time, each zoomable. Chevrons, the arrow keys or a swipe move between them;
 * Escape or the close button returns to the screen.
 */
export function ImageViewer({
  images,
  index,
  onIndexChange,
}: {
  images: ViewerImage[];
  /** The open image, or null when the viewer is closed. */
  index: number | null;
  onIndexChange: (index: number | null) => void;
}) {
  const swipeFrom = useRef<number | null>(null);
  const current = index === null ? undefined : images[index];
  const go = (step: number) => {
    if (index === null) return;
    const next = index + step;
    if (next >= 0 && next < images.length) onIndexChange(next);
  };

  return (
    <D.Root open={!!current} onOpenChange={(open) => !open && onIndexChange(null)}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 animate-fade bg-[var(--scrim)]" />
        <D.Content
          aria-describedby={undefined}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") go(-1);
            if (e.key === "ArrowRight") go(1);
          }}
          className="fixed inset-2 z-50 flex animate-rise flex-col rounded-card bg-sheet shadow-float focus:outline-none sm:inset-6 lg:inset-10"
        >
          <div className="flex h-12 flex-none items-center gap-3 border-b border-border pl-4 pr-1.5">
            <D.Title className="min-w-0 flex-1 truncate text-body font-medium text-ink">{current?.title}</D.Title>
            {images.length > 1 && index !== null && (
              <span className="num flex-none text-meta text-muted">
                {index + 1} of {images.length}
              </span>
            )}
            <D.Close asChild>
              <IconButton aria-label="Close">
                <X />
              </IconButton>
            </D.Close>
          </div>
          <div
            className="relative min-h-0 flex-1 overflow-hidden rounded-b-card"
            onPointerDown={(e) => (swipeFrom.current = e.clientX)}
            onPointerUp={(e) => {
              if (swipeFrom.current === null) return;
              const dx = e.clientX - swipeFrom.current;
              swipeFrom.current = null;
              if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
            }}
          >
            {current && <ZoomableImage key={current.src} src={current.src} alt={current.title} className="h-full animate-fade" />}
            {images.length > 1 && (
              <>
                <ViewerButton side="left" disabled={index === 0} onClick={() => go(-1)} />
                <ViewerButton side="right" disabled={index === images.length - 1} onClick={() => go(1)} />
              </>
            )}
          </div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

function ViewerButton({ side, disabled, onClick }: { side: "left" | "right"; disabled: boolean; onClick: () => void }) {
  const Icon = side === "left" ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      aria-label={side === "left" ? "Previous page" : "Next page"}
      disabled={disabled}
      onClick={onClick}
      className={`absolute top-1/2 ${side === "left" ? "left-2 sm:left-4" : "right-2 sm:right-4"} flex size-10 -translate-y-1/2 items-center justify-center rounded-full border border-border bg-sheet text-ink shadow-float transition-[opacity,transform] hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-0`}
    >
      <Icon className="size-5" />
    </button>
  );
}
