import { Photo } from "./Photo";
import { Pin } from "./Pin";

/**
 * The last screen, pinned: the page flips to its inverted colours, the sentence and the button stay put, and the
 * photograph is drawn up beside them as the page scrolls. Nothing to find or click is held back by the motion.
 */
export function Closing({ primary, signedIn, signIn }: { primary: string; signedIn: boolean; signIn: string }) {
  return (
    <Pin className="close" stageClass="close-stage" screens={1.7} label="Start">
      <div className="close-text">
        <h2 id="cta-h">Start with one. The rest is done.</h2>
        <p>Create your account, make a workspace and show Syruss a few examples.</p>
        <div className="close-actions">
          <a className="button" href={primary}>{signedIn ? "Open Syruss" : "Create your account"}</a>
          {signedIn ? null : <a className="link-quiet" href={signIn}>Sign in</a>}
        </div>
      </div>
      <figure className="close-photo">
        <Photo name="dockside" fill sizes="(max-width: 860px) 100vw, 46vw" className="close-img" />
      </figure>
    </Pin>
  );
}
