import { cn } from "@/lib/format";
import { DEFAULT_OBJECT_ICON, OBJECT_ICONS } from "@/lib/objectIcons";

const SIZES = {
  sm: { tile: "size-6 rounded-[7px]", icon: "size-3.5" },
  md: { tile: "size-9 rounded-control", icon: "size-[18px]" },
  lg: { tile: "size-16 rounded-card", icon: "size-8" },
} as const;

/** An object's picture: its icon on a soft tile. A name not in the list shows the general one. */
export function ObjectIcon({ name, size = "md", className }: { name?: string | null; size?: keyof typeof SIZES; className?: string }) {
  const Icon = OBJECT_ICONS[name ?? ""] ?? OBJECT_ICONS[DEFAULT_OBJECT_ICON];
  const s = SIZES[size];
  return (
    <span aria-hidden className={cn("inline-flex flex-none items-center justify-center bg-[var(--pill-accent-bg)] text-[var(--pill-accent-ink)]", s.tile, className)}>
      <Icon className={s.icon} strokeWidth={1.75} />
    </span>
  );
}
