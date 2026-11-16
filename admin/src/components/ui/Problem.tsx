import { Hourglass } from "lucide-react";
import Link from "next/link";
import { ApprovalPending } from "@/lib/api";
import { cn } from "@/lib/format";

/**
 * Why an action didn't happen. A risky action held for a second owner's approval isn't a failure: it says so calmly and
 * points to where it waits.
 */
export function Problem({ error, className }: { error: Error | null | undefined; className?: string }) {
  if (!error) return null;
  if (error instanceof ApprovalPending) {
    return (
      <p role="status" className={cn("flex items-start gap-1.5 text-meta text-[var(--pill-warning-ink)]", className)}>
        <Hourglass className="mt-0.5 size-3.5 flex-none" aria-hidden />
        <span>
          {error.message} <Link href="/approvals" className="font-medium underline underline-offset-2">See approvals</Link>
        </span>
      </p>
    );
  }
  return <p role="alert" className={cn("text-meta text-negative", className)}>{error.message}</p>;
}
