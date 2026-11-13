"use client";

import { Button } from "@/components/ui/Button";

/** Whatever a page didn't handle itself. A production build never sends the browser the reason, so this stays general. */
export default function ErrorPage({ retry }: { retry: () => void }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 px-4">
      <h1 className="text-title font-semibold text-ink">This page didn&apos;t load</h1>
      <p className="text-body text-muted">Syruss may be down for a moment, or something went wrong on our side. Nothing is lost; try again in a moment.</p>
      <div>
        <Button variant="primary" onClick={() => retry()}>Try again</Button>
      </div>
    </main>
  );
}
