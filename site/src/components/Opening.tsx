import { Photo } from "./Photo";
import { Pin } from "./Pin";
import type { PhotoName } from "@/lib/photos";

/**
 * A photograph that opens: it starts as a framed picture and, as the page scrolls, widens until it fills the screen,
 * with the sentence settling over it. Without script or with reduced motion it is a picture with its words beneath.
 */
export function Opening({ id, title, text, photo }: { id: string; title: string; text: string; photo: PhotoName }) {
  return (
    <Pin className="opening" stageClass="opening-stage" screens={2.4} label={title}>
      <div className="opening-frame">
        <Photo name={photo} fill sizes="100vw" className="opening-img" />
        <div className="opening-scrim" />
      </div>
      <div className="opening-text">
        <h2 id={id}>{title}</h2>
        <p>{text}</p>
      </div>
    </Pin>
  );
}
