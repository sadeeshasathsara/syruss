"use client";

import { ChevronLeft, ChevronRight, ExternalLink, Minus, Plus } from "lucide-react";
import { useState } from "react";
import { IconButton } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Blocks";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";

const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3];

/**
 * The document itself, page by page: pictures as they are, a PDF or TIFF one page at a time (the server makes each
 * page an image). Zoom in and out, or open the page full size in a new tab.
 */
export function DocumentViewer({ id, contentType, pages, name, sizeBytes }: {
  id: string; contentType: string; pages?: number; name?: string | null; sizeBytes?: number;
}) {
  const count = Math.max(1, pages ?? 1);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(2);  // index into ZOOMS: 1 = fit the width
  const [failed, setFailed] = useState<number | null>(null);
  const url = `${api.processes.fileUrl(id)}${page > 1 ? `?page=${page}` : ""}`;
  const scale = ZOOMS[zoom];
  return (
    <Panel title="The document" aside={<span className="truncate text-meta text-muted">{[name, contentType, sizeBytes ? `${Math.round(sizeBytes / 1024)} KB` : null].filter(Boolean).join(" · ")}</span>}>
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1">
          {count > 1 && (
            <>
              <IconButton aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft /></IconButton>
              <span className="num text-meta text-muted">Page {page} of {count}</span>
              <IconButton aria-label="Next page" disabled={page >= count} onClick={() => setPage(page + 1)}><ChevronRight /></IconButton>
            </>
          )}
          <span className="flex-1" />
          <IconButton aria-label="Zoom out" disabled={zoom <= 0} onClick={() => setZoom(zoom - 1)}><Minus /></IconButton>
          <button type="button" onClick={() => setZoom(2)} className="num w-12 text-center text-meta text-muted hover:text-ink" title="Fit the width">
            {Math.round(scale * 100)}%
          </button>
          <IconButton aria-label="Zoom in" disabled={zoom >= ZOOMS.length - 1} onClick={() => setZoom(zoom + 1)}><Plus /></IconButton>
          <a href={url} target="_blank" rel="noreferrer" aria-label="Open full size" title="Open full size"
            className="inline-flex size-8 items-center justify-center rounded-control text-muted hover:bg-raised hover:text-ink">
            <ExternalLink className="size-4" />
          </a>
        </div>
        <div className="max-h-[70vh] overflow-auto rounded-control border border-border bg-raised scrollbar-thin">
          {failed === page ? (
            <p className="p-4 text-body text-muted">This page couldn&apos;t be shown here. <a href={url} target="_blank" rel="noreferrer" className="text-ink underline">Open the file</a> instead.</p>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={url} src={url} alt={`${name ?? "The document"}, page ${page}`} onError={() => setFailed(page)}
              style={{ width: `${scale * 100}%`, maxWidth: "none" }} className={cn("block h-auto bg-white", scale <= 1 && "mx-auto")} />
          )}
        </div>
      </div>
    </Panel>
  );
}
