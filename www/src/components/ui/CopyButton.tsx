"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "./Button";

/** Copies text and says so for a moment. */
export function CopyButton({ value, label = "Copy", size = "md" }: { value: string; label?: string; size?: "sm" | "md" }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <Button
      variant="secondary"
      size={size}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
        } catch {
          /* clipboard blocked: the text is still on screen to select */
        }
      }}
    >
      {copied ? <Check /> : <Copy />} {copied ? "Copied" : label}
    </Button>
  );
}
