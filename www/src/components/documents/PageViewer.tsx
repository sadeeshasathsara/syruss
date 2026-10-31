"use client";

import { Expand } from "lucide-react";
import { useState } from "react";
import { ImageViewer } from "@/components/ui/ImageViewer";
import { ToolButton, ZoomableImage } from "@/components/ui/ZoomableImage";
import { cn } from "@/lib/format";

/**
 * The scanned page, fitted inside its column. Zoom with the mouse wheel or pinch, drag to move;
 * the expand button opens it full screen, where it zooms the same way.
 */
export function PageViewer({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <figure className={cn("h-[min(78dvh,880px)] min-h-80 overflow-hidden rounded-card border border-border", className)}>
      <ZoomableImage
        src={src}
        alt={alt}
        className="h-full"
        actions={
          <ToolButton label="Open full screen" onClick={() => setOpen(0)}>
            <Expand />
          </ToolButton>
        }
      />
      <ImageViewer images={[{ src, title: alt }]} index={open} onIndexChange={setOpen} />
    </figure>
  );
}
