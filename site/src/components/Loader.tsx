
/** One loading state everywhere: a page being read, line by line, with what is happening in words. */
export function Loader({ label = "Loading" }: { label?: string }) {
  return (
    <div className={"loader"} role="status" aria-live="polite">
      <svg className="loader-scan" viewBox="0 0 84 100" width="84" height="100" aria-hidden="true">
        <path className="loader-scan-page" d="M10 4h46l18 18v72a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4V8a4 4 0 0 1 4-4z" />
        <path className="loader-scan-fold" d="M56 4v14a4 4 0 0 0 4 4h14" />
        <rect className="loader-scan-line" x="16" y="34" width="46" height="5" rx="2.5" style={{ animationDelay: "0.35s" }} />
        <rect className="loader-scan-line" x="16" y="46" width="52" height="5" rx="2.5" style={{ animationDelay: "0.8s" }} />
        <rect className="loader-scan-line" x="16" y="58" width="38" height="5" rx="2.5" style={{ animationDelay: "1.25s" }} />
        <rect className="loader-scan-line" x="16" y="70" width="48" height="5" rx="2.5" style={{ animationDelay: "1.7s" }} />
        <rect className="loader-scan-beam" x="8" y="22" width="68" height="3" rx="1.5" />
      </svg>
      <p className="loader-label">{label}…</p>
    </div>
  );
}
