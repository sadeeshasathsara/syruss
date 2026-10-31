"use client";

import { useEffect, useRef } from "react";

/**
 * A pinned section: the page holds still on a full-height stage while scrolling drives what happens on it.
 *
 * The section is `screens` viewports tall and its stage is sticky, so ordinary scrolling is the timeline. Progress
 * (0 at the top, 1 at the end) is written straight to the section as CSS variables, so the pictures and words move
 * without React re-rendering anything:
 *   --p  progress, 0 to 1
 *   --s  the step you are at, 0 to (steps - 1), fractional between steps
 * `onProgress` (for the one section that types text) and `onStep` (for the step list) are the only React hooks in.
 * With reduced motion the stage is not pinned at all: the CSS lays every step out in turn.
 */
export function Pin({ id, screens, steps = 1, hold = 0, className = "", stageClass = "", label, onProgress, onStep, children }: {
  id?: string;
  screens: number;
  steps?: number;
  /** Share of each step spent resting, 0 to 0.4: the picture holds still, then moves quickly to the next. */
  hold?: number;
  className?: string;
  stageClass?: string;
  label?: string;
  onProgress?: (p: number) => void;
  onStep?: (step: number) => void;
  children: React.ReactNode;
}) {
  const root = useRef<HTMLElement>(null);
  const last = useRef({ p: -1, step: -1 });

  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    const update = () => {
      frame = 0;
      const span = el.offsetHeight - window.innerHeight;
      const top = el.getBoundingClientRect().top;
      const p = span > 0 ? Math.min(1, Math.max(0, -top / span)) : 0;
      if (Math.abs(p - last.current.p) < 0.0005) return;
      last.current.p = p;
      el.style.setProperty("--p", p.toFixed(4));
      const raw = p * Math.max(0, steps - 1);
      const whole = Math.min(Math.floor(raw), Math.max(0, steps - 2));
      const eased = hold > 0 && steps > 1 ? whole + Math.min(1, Math.max(0, (raw - whole - hold) / (1 - 2 * hold))) : raw;
      el.style.setProperty("--s", eased.toFixed(4));
      onProgress?.(p);
      const step = Math.round(eased);
      if (step !== last.current.step) {
        last.current.step = step;
        onStep?.(step);
      }
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    const start = () => {
      if (reduced.matches) {
        el.style.removeProperty("--p");
        el.style.removeProperty("--s");
        return;
      }
      update();
    };
    start();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    reduced.addEventListener("change", start);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      reduced.removeEventListener("change", start);
      if (frame) cancelAnimationFrame(frame);
    };
    // The callbacks are read through the latest render on every frame; the listeners stay put.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, hold]);

  return (
    <section id={id} ref={root} className={`pin ${className}`} style={{ ["--screens" as string]: screens, ["--n" as string]: steps }} aria-label={label}>
      <div className={`pin-stage ${stageClass}`}>{children}</div>
    </section>
  );
}

/** Scrolls so that a pinned section sits at `progress` (0 to 1): how a step button or a question chip takes you there. */
export function jumpTo(from: HTMLElement, progress: number): void {
  const section = from.closest<HTMLElement>(".pin");
  if (!section) return;
  const top = section.getBoundingClientRect().top + window.scrollY;
  const span = section.offsetHeight - window.innerHeight;
  window.scrollTo({ top: top + Math.min(1, Math.max(0, progress)) * span + 1, behavior: "smooth" });
}
