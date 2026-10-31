"use client";

import { useEffect, useRef, useState } from "react";
import { Photo } from "./Photo";

const FRAMES = [
  { id: "receipts", label: "Receipts, invoices and delivery notes arrive in every shape", node: <Photo name="receipts" fill sizes="40vw" className="reel-photo" priority /> },
  { id: "invoice", label: "Syruss reads each one into tidy details", node: <Photo name="invoice-paris" fill sizes="40vw" className="reel-photo" /> },
  { id: "post-room", label: "And sends them on to the systems you already use", node: <Photo name="post-room" fill sizes="40vw" className="reel-photo" /> },
];

/**
 * The sentence's centre: a strip of three frames passing through it. The page holds still (position: sticky)
 * for about one screen per frame; ordinary scrolling advances the strip. Without script, or with reduced
 * motion, the first frame stays and the page is its normal height.
 */
export function HeroReel({ children }: { children: React.ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    root.current?.setAttribute("data-pinned", "true");
    let frame = 0;
    const update = () => {
      frame = 0;
      const el = root.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const span = rect.height - window.innerHeight;
      const progress = span > 0 ? Math.min(1, Math.max(0, -rect.top / span)) : 0;
      setStep(Math.min(FRAMES.length - 1, Math.floor(progress * FRAMES.length)));
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div className="hero-pin" ref={root} style={{ ["--step" as string]: step }}>
      <div className="hero-stage">
        <div className="hero-reel" aria-live="polite">
          <div className="reel-strip">
            {FRAMES.map((f, i) => (
              <figure className="reel-frame" key={f.id} aria-hidden={i !== step} data-active={i === step}>
                {f.node}
                <figcaption>{f.label}</figcaption>
              </figure>
            ))}
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}
