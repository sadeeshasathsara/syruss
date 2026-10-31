"use client";

import { Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/format";

const MIN = 1;
const MAX = 8;

type View = { scale: number; x: number; y: number };
const FIT: View = { scale: 1, x: 0, y: 0 };

/**
 * An image fitted to its box that you can read closely: the wheel zooms at the pointer,
 * dragging pans, two fingers pinch, double-click toggles a close look, and + / - / 0 work
 * from the keyboard. Pointer events stop here while zoomed, so a parent's swipe doesn't fire.
 */
export function ZoomableImage({ src, alt, className, actions }: { src: string; alt: string; className?: string; actions?: React.ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<View>(FIT);
  const [failed, setFailed] = useState(false);
  const [moving, setMoving] = useState(false);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ distance: number; scale: number } | null>(null);

  /** Zoom to `next` keeping the point (px, py) of the box where it is. */
  const zoomAt = useCallback((next: number, px?: number, py?: number) => {
    const el = box.current;
    if (!el) return;
    const { width: w, height: h } = el.getBoundingClientRect();
    setView((v) => {
      const scale = Math.min(MAX, Math.max(MIN, next));
      const cx = px ?? w / 2;
      const cy = py ?? h / 2;
      const x = cx - ((cx - v.x) * scale) / v.scale;
      const y = cy - ((cy - v.y) * scale) / v.scale;
      return clamp({ scale, x, y }, w, h);
    });
  }, []);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      setView((v) => {
        const scale = Math.min(MAX, Math.max(MIN, v.scale * Math.exp(-e.deltaY * 0.0015)));
        const px = e.clientX - r.left;
        const py = e.clientY - r.top;
        return clamp({ scale, x: px - ((px - v.x) * scale) / v.scale, y: py - ((py - v.y) * scale) / v.scale }, r.width, r.height);
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const local = (e: React.PointerEvent | React.MouseEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const zoomed = view.scale > 1.001;

  return (
    <div className={cn("relative overflow-hidden bg-raised", className)}>
      <div
        ref={box}
        tabIndex={0}
        role="img"
        aria-label={`${alt}. Scroll or pinch to zoom, drag to move.`}
        className={cn("h-full w-full touch-none select-none outline-none", zoomed ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in")}
        onKeyDown={(e) => {
          if (e.key === "+" || e.key === "=") zoomAt(view.scale * 1.5);
          if (e.key === "-") zoomAt(view.scale / 1.5);
          if (e.key === "0") setView(FIT);
        }}
        onDoubleClick={(e) => {
          const p = local(e);
          if (zoomed) setView(FIT);
          else zoomAt(2.5, p.x, p.y);
        }}
        onPointerDown={(e) => {
          if (zoomed) e.stopPropagation();
          (e.target as Element).setPointerCapture?.(e.pointerId);
          setMoving(true);
          pointers.current.set(e.pointerId, local(e));
          if (pointers.current.size === 2) {
            const [a, b] = [...pointers.current.values()];
            pinch.current = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: view.scale };
            drag.current = null;
          } else {
            const p = local(e);
            drag.current = { x: p.x, y: p.y, vx: view.x, vy: view.y };
          }
        }}
        onPointerMove={(e) => {
          if (!pointers.current.has(e.pointerId)) return;
          if (zoomed) e.stopPropagation();
          pointers.current.set(e.pointerId, local(e));
          const r = box.current!.getBoundingClientRect();
          if (pinch.current && pointers.current.size === 2) {
            const [a, b] = [...pointers.current.values()];
            zoomAt((pinch.current.scale * Math.hypot(a.x - b.x, a.y - b.y)) / pinch.current.distance, (a.x + b.x) / 2, (a.y + b.y) / 2);
          } else if (drag.current && zoomed) {
            const p = local(e);
            const d = drag.current;
            setView((v) => clamp({ ...v, x: d.vx + p.x - d.x, y: d.vy + p.y - d.y }, r.width, r.height));
          }
        }}
        onPointerUp={(e) => {
          if (zoomed) e.stopPropagation();
          pointers.current.delete(e.pointerId);
          if (pointers.current.size < 2) pinch.current = null;
          if (pointers.current.size === 0) {
            drag.current = null;
            setMoving(false);
          }
        }}
        onPointerCancel={(e) => {
          pointers.current.delete(e.pointerId);
          pinch.current = null;
          drag.current = null;
          setMoving(false);
        }}
      >
        {failed ? (
          <p className="p-6 text-body text-muted">The page image couldn’t be loaded.</p>
        ) : (
          <div
            className="h-full w-full origin-top-left will-change-transform"
            style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`, transition: moving ? "none" : "transform 120ms ease-out" }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={alt} draggable={false} onError={() => setFailed(true)} className="h-full w-full object-contain" />
          </div>
        )}
      </div>

      <div className="absolute right-2 top-2 flex items-center gap-1 rounded-control border border-border bg-sheet p-0.5 shadow-float">
        <ToolButton label="Zoom out" disabled={!zoomed} onClick={() => zoomAt(view.scale / 1.5)}>
          <Minus />
        </ToolButton>
        <button
          type="button"
          onClick={() => setView(FIT)}
          title="Fit the page"
          className="num h-7 min-w-12 rounded-chip px-1 text-meta text-muted hover:bg-raised hover:text-ink pointer-coarse:h-9"
        >
          {Math.round(view.scale * 100)}%
        </button>
        <ToolButton label="Zoom in" disabled={view.scale >= MAX} onClick={() => zoomAt(view.scale * 1.5)}>
          <Plus />
        </ToolButton>
        {actions}
      </div>
    </div>
  );
}

function clamp(v: View, w: number, h: number): View {
  return { scale: v.scale, x: Math.min(0, Math.max(w - w * v.scale, v.x)), y: Math.min(0, Math.max(h - h * v.scale, v.y)) };
}

export function ToolButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="flex size-7 items-center justify-center rounded-chip text-muted transition-colors hover:bg-raised hover:text-ink disabled:opacity-40 pointer-coarse:size-9 [&_svg]:size-4"
    >
      {children}
    </button>
  );
}
