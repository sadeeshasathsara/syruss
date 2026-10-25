import Link from "next/link";

/** The Syruss mark: a page with a margin line, drawn in ink with the data hue on the read line. */
export function BrandMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden className="flex-none">
      <rect x="3.5" y="1.5" width="13" height="17" rx="2" fill="none" stroke="var(--ink)" strokeWidth="1.5" />
      <path d="M6.5 6h7M6.5 9.5h7" stroke="var(--ink)" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M6.5 13h4.5" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function Brand({ href }: { href: string }) {
  return (
    <Link href={href} className="flex items-center gap-2 rounded-control px-1 py-1 text-section font-semibold text-ink">
      <BrandMark />
      Syruss
    </Link>
  );
}
