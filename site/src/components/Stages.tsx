"use client";

import { useRef, useState } from "react";
import { jumpTo, Pin } from "./Pin";
import { Photo } from "./Photo";
import type { PhotoName } from "@/lib/photos";

const STAGES: { name: string; clause: string; sub: string; photo: PhotoName }[] = [
  { name: "Show a few", clause: "Show a few. It learns what to do.", sub: "Upload a handful of the documents you receive. They can look different from each other.", photo: "invoice-london" },
  { name: "Tick the parts", clause: "Tick the parts. That’s how it starts.", sub: "Syruss suggests the details to pull out, such as the date, the supplier and the total. You tick what you want.", photo: "invoice-new-york" },
  { name: "Read complete", clause: "Every new sheet, read complete.", sub: "New documents are read the day they arrive. If something looks wrong, it asks you instead of guessing.", photo: "card-room" },
  { name: "Sent on its way", clause: "Sent the same day, on its way.", sub: "Every document is saved, and the details go on to the systems you already use, automatically.", photo: "dockside" },
];

/**
 * How it works, pinned: the page holds still while scrolling turns the four steps. The words rise out as the next
 * ones arrive and each photograph is drawn up over the one before it. Without script or with reduced motion the four
 * steps simply follow one another down the page.
 */
export function Stages() {
  const [i, setI] = useState(0);
  const list = useRef<HTMLOListElement>(null);
  return (
    <Pin id="how-it-works" className="stages" stageClass="stages-stage" screens={5} steps={STAGES.length} hold={0.28} onStep={setI} label="How it works">
      <h2 className="sr-only">How it works</h2>
      {STAGES.map((s, n) => (
        <article className="stages-item" key={s.name} style={{ ["--i" as string]: n, ["--k" as string]: n - 1 }}>
          <div className="stages-copy">
            <h3 className="stages-clause">{s.clause}</h3>
            <p className="stages-sub">{s.sub}</p>
          </div>
          <div className="stages-photo">
            <Photo name={s.photo} fill sizes="(max-width: 860px) 100vw, 50vw" className="stages-img" priority={n === 0} />
          </div>
        </article>
      ))}
      <ol className="stages-steps" ref={list}>
        {STAGES.map((s, n) => (
          <li key={s.name} style={{ ["--i" as string]: n }}>
            <button type="button" onClick={(e) => jumpTo(e.currentTarget, n / (STAGES.length - 1))} aria-current={n === i ? "step" : undefined}>
              <span className="stages-bar" aria-hidden="true"><span className="stages-fill" /></span>
              <span className="stages-name">{s.name}</span>
            </button>
          </li>
        ))}
      </ol>
    </Pin>
  );
}
