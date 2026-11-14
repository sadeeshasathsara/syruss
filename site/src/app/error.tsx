"use client";

import { AccountShell } from "@/components/AccountShell";

/** Whatever a page didn't handle itself. A production build never sends the browser the reason, so this stays general. */
export default function ErrorPage({ retry }: { retry: () => void }) {
  return (
    <AccountShell signedIn={false}>
      <div className="sheet">
        <h1>This page didn&apos;t load</h1>
        <p className="lede">Syruss may be down for a moment, or something went wrong on our side. Nothing is lost; try again in a moment.</p>
        <button type="button" className="button" onClick={() => retry()}>Try again</button>
      </div>
    </AccountShell>
  );
}
