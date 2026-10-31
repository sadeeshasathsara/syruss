/** The Syruss mark: a page with a margin line and a teal read line (the same mark as the app). */
export function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect x="5" y="3" width="22" height="26" rx="3" fill="currentColor" />
      <rect x="9" y="3" width="2.4" height="26" fill="var(--sheet)" opacity="0.9" />
      <rect x="13.5" y="13" width="10" height="3" fill="var(--accent)" />
    </svg>
  );
}

export function Wordmark({ size = 24 }: { size?: number }) {
  return (
    <span className="wordmark">
      <BrandMark size={size} />
      <span>Syruss</span>
    </span>
  );
}
