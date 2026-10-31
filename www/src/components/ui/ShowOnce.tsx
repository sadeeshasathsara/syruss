"use client";

import { Button } from "./Button";
import { CopyButton } from "./CopyButton";

/** A secret shown once, with a copy button and a clear warning. */
export function ShowOnce({ label, value, onDone }: { label: string; value: string; onDone: () => void }) {
  return (
    <div className="flex animate-rise flex-col gap-3 rounded-card border border-control bg-card p-4">
      <p className="text-body font-medium text-ink">{label}</p>
      <p className="text-body text-muted">Copy it now. For your security it won’t be shown again.</p>
      <div className="flex min-w-0 items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink">{value}</code>
        <CopyButton value={value} />
      </div>
      <div>
        <Button variant="primary" onClick={onDone}>I’ve saved it</Button>
      </div>
    </div>
  );
}
