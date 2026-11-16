"use client";

import { FileText, ImageIcon, UploadCloud, X } from "lucide-react";
import { useId, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/format";

const size = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

/**
 * Choosing files to send: a clear area to drop them on or click, a button for keyboards and screen readers, and the
 * chosen files listed with a way to take one back. Files of another type, or over the size limit, are refused here
 * with the reason (the server checks again).
 */
export function FileDrop({ label, help, accept, maxFiles, maxBytes, files, onChange }: {
  label: string; help: string; accept: string[]; maxFiles: number; maxBytes: number; files: File[]; onChange: (files: File[]) => void;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);

  const add = (picked: File[]) => {
    const wrongType = picked.filter((f) => !accept.includes(f.type));
    const tooBig = picked.filter((f) => accept.includes(f.type) && f.size > maxBytes);
    const ok = picked.filter((f) => accept.includes(f.type) && f.size <= maxBytes);
    const next = [...files, ...ok.filter((f) => !files.some((g) => g.name === f.name && g.size === f.size))].slice(0, maxFiles);
    const reasons = [
      wrongType.length ? `${wrongType.map((f) => f.name).join(", ")}: not a PDF or a photo` : "",
      tooBig.length ? `${tooBig.map((f) => f.name).join(", ")}: over ${size(maxBytes)}` : "",
      files.length + ok.length > maxFiles ? `at most ${maxFiles} files` : "",
    ].filter(Boolean);
    setRefused(reasons.length ? `Not added: ${reasons.join("; ")}.` : null);
    onChange(next);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-body font-medium text-ink">{label}</label>
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(Array.from(e.dataTransfer.files)); }}
        onClick={() => input.current?.click()}
        className={cn("flex cursor-pointer flex-col items-center gap-2 rounded-card border-2 border-dashed px-4 py-6 text-center transition-colors",
          over ? "border-accent bg-[var(--pill-accent-bg)]" : "border-control bg-raised hover:border-accent")}
      >
        <UploadCloud className="size-7 text-muted" aria-hidden />
        <p className="text-body text-ink">Drop the slip here, or</p>
        <Button type="button" variant="secondary" size="sm" onClick={(e) => { e.stopPropagation(); input.current?.click(); }}>Choose files</Button>
        <p className="text-meta text-muted">{help}</p>
        <input ref={input} id={id} type="file" multiple accept={accept.join(",")} className="sr-only"
          onChange={(e) => { add(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
      </div>
      {refused && <p className="text-meta text-negative">{refused}</p>}
      {!!files.length && (
        <ul className="flex flex-col divide-y divide-rule rounded-card border border-border">
          {files.map((f, n) => (
            <li key={`${f.name}-${f.size}`} className="flex items-center gap-2 px-3 py-2">
              {f.type === "application/pdf" ? <FileText className="size-4 flex-none text-muted" aria-hidden /> : <ImageIcon className="size-4 flex-none text-muted" aria-hidden />}
              <span className="min-w-0 flex-1 truncate text-body text-ink">{f.name}</span>
              <span className="text-meta text-muted">{size(f.size)}</span>
              <button type="button" aria-label={`Remove ${f.name}`} onClick={() => onChange(files.filter((_, k) => k !== n))}
                className="rounded-control p-1 text-muted hover:bg-raised hover:text-ink">
                <X className="size-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
