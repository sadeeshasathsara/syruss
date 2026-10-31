"use client";

import { Upload, X } from "lucide-react";
import { useRef, useState } from "react";
import { cn, fileSize } from "@/lib/format";

export const ACCEPTED = "image/jpeg,image/png,image/tiff,image/webp";

/** Drop files or pick them; lists what is chosen before anything is sent. */
export function Dropzone({
  files,
  onChange,
  max,
  hint,
}: {
  files: File[];
  onChange: (files: File[]) => void;
  max?: number;
  hint: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);

  const add = (list: FileList | null) => {
    if (!list) return;
    const incoming = [...list];
    const ok = incoming.filter((f) => ACCEPTED.split(",").includes(f.type));
    setRejected(ok.length < incoming.length ? "Only JPEG, PNG, TIFF and WebP images can be read for now." : null);
    const merged = [...files, ...ok.filter((f) => !files.some((g) => g.name === f.name && g.size === f.size))];
    onChange(max ? merged.slice(0, max) : merged);
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          add(e.dataTransfer.files);
        }}
        className={cn(
          "flex flex-col items-start gap-2 rounded-card border border-dashed px-4 py-5 sm:flex-row sm:items-center sm:justify-between",
          over ? "border-accent bg-accent-soft" : "border-control bg-raised",
        )}
      >
        <div>
          <p className="text-body font-medium text-ink">Drop images here</p>
          <p className="text-body text-muted">{hint}</p>
        </div>
        <button
          type="button"
          onClick={() => input.current?.click()}
          className="inline-flex h-8 items-center gap-1.5 rounded-control border border-control bg-sheet px-3 text-body font-medium text-ink hover:bg-raised pointer-coarse:h-10"
        >
          <Upload className="size-4" /> Choose files
        </button>
        <input
          ref={input}
          type="file"
          accept={ACCEPTED}
          multiple
          className="sr-only"
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {rejected && <p className="text-meta text-negative">{rejected}</p>}
      {files.length > 0 && (
        <ul className="flex flex-col">
          {files.map((f, i) => (
            <li key={`${f.name}-${f.size}`} className="flex items-center gap-3 border-b border-rule py-2">
              <span className="min-w-0 flex-1 truncate text-body text-ink">{f.name}</span>
              <span className="num text-meta text-muted">{fileSize(f.size)}</span>
              <button
                type="button"
                aria-label={`Remove ${f.name}`}
                onClick={() => onChange(files.filter((_, j) => j !== i))}
                className="flex size-7 items-center justify-center rounded-control text-muted hover:bg-raised hover:text-ink"
              >
                <X className="size-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {max && files.length >= max && <p className="text-meta text-muted">Up to {max} at a time.</p>}
    </div>
  );
}
