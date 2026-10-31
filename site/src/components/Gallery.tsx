import { Photo } from "./Photo";
import type { PhotoName } from "@/lib/photos";

/** Where each document lies in the pile: left and top as a share of the pile, its width, and how far it is turned. */
const LAYOUT: { x: number; y: number; w: number; r: number }[] = [
  { x: 2, y: 4, w: 44, r: -5 },
  { x: 50, y: 0, w: 46, r: 4 },
  { x: 6, y: 34, w: 40, r: -2 },
  { x: 40, y: 22, w: 36, r: 6 },
  { x: 54, y: 46, w: 42, r: -3 },
  { x: 2, y: 62, w: 46, r: 3 },
  { x: 36, y: 56, w: 34, r: -6 },
];

/**
 * The paperwork Syruss reads, laid out as one pile of real documents beside the sentence. The pile settles into
 * place as it scrolls into view, and a document comes to the top when it is pointed at.
 */
export function Gallery({ id, title, text, names }: { id: string; title: string; text: string; names: PhotoName[] }) {
  return (
    <section className="any" id="documents" aria-labelledby={id}>
      <div className="any-text" data-reveal>
        <h2 id={id}>{title}</h2>
        <p>{text}</p>
      </div>
      <div className="any-pile" data-reveal role="group" aria-label="Examples of the paperwork Syruss reads">
        {names.map((n, k) => {
          const at = LAYOUT[k % LAYOUT.length];
          return (
            <figure
              className="any-card"
              key={n}
              style={{ ["--x" as string]: `${at.x}%`, ["--y" as string]: `${at.y}%`, ["--w" as string]: `${at.w}%`, ["--r" as string]: `${at.r}deg`, ["--k" as string]: k }}
            >
              <Photo name={n} sizes="(max-width: 860px) 46vw, 24vw" className="any-photo" />
            </figure>
          );
        })}
      </div>
    </section>
  );
}
