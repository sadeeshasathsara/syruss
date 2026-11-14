"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Expand } from "lucide-react";
import { useState } from "react";
import { ImageViewer } from "@/components/ui/ImageViewer";
import { ToolButton, ZoomableImage } from "@/components/ui/ZoomableImage";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { DocumentItem } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

/** Shown by a browser as they are. A PDF or TIFF is drawn page by page on the server; a picture is always one image. */
const PICTURES = ["image/jpeg", "image/png", "image/webp"];

/** The pages there are to look at (a picture counted as several sheets is still one image). */
export function pagesToShow(doc: Pick<DocumentItem, "content_type" | "page_count"> | undefined): number {
  return !doc || PICTURES.includes(doc.content_type) ? 1 : Math.max(1, doc.page_count ?? 1);
}

const pageSrc = (src: string, page: number) => (page > 1 ? `${src}${src.includes("?") ? "&" : "?"}page=${page}` : src);

/**
 * The scanned page, fitted inside its column. Zoom with the mouse wheel or pinch, drag to move; the expand button opens
 * it full screen, where it zooms the same way. A document with several pages gets previous and next.
 */
export function PageViewer({ src, alt, className, pages = 1 }: { src: string; alt: string; className?: string; pages?: number }) {
  const [open, setOpen] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const at = Math.min(page, pages);
  const all = Array.from({ length: pages }, (_, i) => ({ src: pageSrc(src, i + 1), title: pages > 1 ? `${alt}, page ${i + 1} of ${pages}` : alt }));
  return (
    <figure className={cn("h-[min(78dvh,880px)] min-h-80 overflow-hidden rounded-card border border-border", className)}>
      <ZoomableImage
        key={at}
        src={all[at - 1].src}
        alt={all[at - 1].title}
        className="h-full"
        actions={
          <>
            {pages > 1 && (
              <>
                <ToolButton label="Previous page" disabled={at <= 1} onClick={() => setPage(at - 1)}>
                  <ChevronLeft />
                </ToolButton>
                <span className="px-1 text-meta tabular-nums text-muted" aria-live="polite">
                  {at} / {pages}
                </span>
                <ToolButton label="Next page" disabled={at >= pages} onClick={() => setPage(at + 1)}>
                  <ChevronRight />
                </ToolButton>
              </>
            )}
            <ToolButton label="Open full screen" onClick={() => setOpen(at - 1)}>
              <Expand />
            </ToolButton>
          </>
        }
      />
      <ImageViewer images={all} index={open} onIndexChange={(i) => { setOpen(i); if (i !== null) setPage(i + 1); }} />
    </figure>
  );
}

/** A document's pages, for places that only know its id: looks up how many pages it has. */
export function DocumentViewer({ documentId, alt = "Scanned page", className }: { documentId: string; alt?: string; className?: string }) {
  const { workspace } = useWorkspace();
  const doc = useQuery({ queryKey: ["documents", workspace.id, documentId], queryFn: () => api.documents.get(workspace.id, documentId) });
  return <PageViewer key={documentId} src={api.documents.fileUrl(workspace.id, documentId)} alt={alt} className={className} pages={pagesToShow(doc.data)} />;
}
