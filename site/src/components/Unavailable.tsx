import { AccountShell } from "./AccountShell";

/** In place of a page when the API doesn't answer: what happened, that nothing is lost, and the same page again. */
export function Unavailable({ again, signedIn = true }: { again: string; signedIn?: boolean }) {
  return (
    <AccountShell signedIn={signedIn}>
      <div className="sheet">
        <h1>Syruss can&apos;t be reached right now</h1>
        <p className="lede">It&apos;s usually back within a few minutes, and nothing you&apos;ve done is lost.</p>
        <a className="button" href={again}>Try again</a>
      </div>
    </AccountShell>
  );
}
