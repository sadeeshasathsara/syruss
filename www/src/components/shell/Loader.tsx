
/** One loading state everywhere: a page being read, line by line, with what is happening in words. */
export function Loader({ label = "Loading", fill = true }: { label?: string; fill?: boolean }) {
  return (
    <div className={fill ? "syruss-loader syruss-loader-fill" : "syruss-loader"} role="status" aria-live="polite">
      <svg className="syruss-loader-scan" viewBox="0 0 84 100" width="84" height="100" aria-hidden="true">
        <path className="syruss-loader-scan-page" d="M10 4h46l18 18v72a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4V8a4 4 0 0 1 4-4z" />
        <path className="syruss-loader-scan-fold" d="M56 4v14a4 4 0 0 0 4 4h14" />
        <rect className="syruss-loader-scan-line" x="16" y="34" width="46" height="5" rx="2.5" style={{ animationDelay: "0.35s" }} />
        <rect className="syruss-loader-scan-line" x="16" y="46" width="52" height="5" rx="2.5" style={{ animationDelay: "0.8s" }} />
        <rect className="syruss-loader-scan-line" x="16" y="58" width="38" height="5" rx="2.5" style={{ animationDelay: "1.25s" }} />
        <rect className="syruss-loader-scan-line" x="16" y="70" width="48" height="5" rx="2.5" style={{ animationDelay: "1.7s" }} />
        <rect className="syruss-loader-scan-beam" x="8" y="22" width="68" height="3" rx="1.5" />
      </svg>
      <p className="syruss-loader-label">{label}…</p>
    </div>
  );
}
