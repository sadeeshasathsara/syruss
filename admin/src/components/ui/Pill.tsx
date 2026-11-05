import { cn } from "@/lib/format";

export type Tone = "neutral" | "positive" | "negative" | "warning" | "info" | "accent";

const tones: Record<Tone, string> = {
  neutral: "bg-[var(--pill-neutral-bg)] text-[var(--pill-neutral-ink)]",
  positive: "bg-[var(--pill-positive-bg)] text-[var(--pill-positive-ink)]",
  negative: "bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]",
  warning: "bg-[var(--pill-warning-bg)] text-[var(--pill-warning-ink)]",
  info: "bg-[var(--pill-info-bg)] text-[var(--pill-info-ink)]",
  accent: "bg-[var(--pill-accent-bg)] text-[var(--pill-accent-ink)]",
};

export function Pill({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex h-5 flex-none items-center whitespace-nowrap rounded-chip px-1.5 text-meta font-medium", tones[tone], className)}>
      {children}
    </span>
  );
}
