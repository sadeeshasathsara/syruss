"use client";

import Link from "next/link";
import type { RuleDryRun } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

/** What a rule says about the records already saved: how many pass, fail or don't apply, and where it fails. */
export function DryRun({ run, compact }: { run: RuleDryRun; compact?: boolean }) {
  const { href } = useWorkspace();
  if (run.records === 0) {
    return <p className="text-body text-positive">It compiles. There are no saved records yet to try it on.</p>;
  }
  const parts = [
    `passes on ${run.passed}`,
    run.failed ? `fails on ${run.failed}` : "",
    run.not_applicable ? `doesn't apply to ${run.not_applicable}` : "",
  ].filter(Boolean);
  return (
    <div className="flex flex-col gap-1">
      <p className={run.failed ? "text-body text-ink" : "text-body text-positive"}>
        It compiles. On the last {run.records} saved record{run.records === 1 ? "" : "s"} it {parts.join(", ")}.
      </p>
      {!compact && run.failures.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {run.failures.map((f) => (
            <li key={f.document_id} className="text-body">
              <Link href={href(`documents/${f.document_id}`)} className="text-ink underline underline-offset-2">{f.name}</Link>
              {f.rows.length > 0 && <span className="ml-2 text-meta text-muted">{f.rows.join(", ")}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
