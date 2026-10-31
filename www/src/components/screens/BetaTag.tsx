import { cn } from "@/lib/format";

/** Analytics is still being shaped: this says so, wherever its pages show a title. */
export function BetaTag({ className }: { className?: string }) {
  return (
    <span
      title="Analytics is in beta: it works, and it will keep changing as people use it."
      className={cn("ml-2 inline-block rounded-chip bg-[var(--pill-info-bg)] px-1.5 align-middle text-meta font-medium text-[var(--pill-info-ink)]", className)}
    >
      Beta
    </span>
  );
}
