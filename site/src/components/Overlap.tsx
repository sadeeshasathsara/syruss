import { Photo } from "./Photo";
import { Pin } from "./Pin";
import type { PhotoName } from "@/lib/photos";

type Panel = { id: string; anchor?: string; title: string; text: string; photo: PhotoName };

/**
 * Two statements laid one over the other: the page holds still on the first, and as it scrolls the second slides
 * up and covers it. Without script or with reduced motion they follow one another.
 */
export function Overlap({ panels }: { panels: [Panel, Panel] }) {
  return (
    <Pin className="overlap" stageClass="overlap-stage" screens={2.6} steps={2} hold={0.2}>
      {panels.map((p, n) => (
        <section className="overlap-panel" id={p.anchor} aria-labelledby={p.id} key={p.id} style={{ ["--i" as string]: n }}>
          <div className="overlap-copy">
            <h2 id={p.id}>{p.title}</h2>
            <p>{p.text}</p>
          </div>
          <figure className="overlap-photo">
            <Photo name={p.photo} fill sizes="(max-width: 860px) 100vw, 50vw" className="overlap-img" />
          </figure>
        </section>
      ))}
    </Pin>
  );
}
