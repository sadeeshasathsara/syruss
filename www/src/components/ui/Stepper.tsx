import { Check } from "lucide-react";
import { cn } from "@/lib/format";

/** The steps of a task done in order. The current one is named in full; on a phone only it and its place in the order show. */
export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <nav aria-label="Progress">
      <p className="text-body text-muted sm:hidden">Step {current + 1} of {steps.length}: <span className="font-medium text-ink">{steps[current]}</span></p>
      <ol className="hidden items-center gap-2 sm:flex">
        {steps.map((label, i) => (
          <li key={label} className="flex items-center gap-2" aria-current={i === current ? "step" : undefined}>
            <span
              className={cn(
                "num flex size-6 flex-none items-center justify-center rounded-full text-meta font-medium",
                i < current ? "bg-primary text-primary-ink" : i === current ? "border border-ink text-ink" : "border border-border text-muted",
              )}
            >
              {i < current ? <Check className="size-3.5" /> : i + 1}
            </span>
            <span className={cn("text-body", i === current ? "font-medium text-ink" : "text-muted")}>{label}</span>
            {i < steps.length - 1 && <span aria-hidden className="mx-1 h-px w-6 bg-border" />}
          </li>
        ))}
      </ol>
    </nav>
  );
}
