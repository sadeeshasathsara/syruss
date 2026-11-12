"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

/**
 * A thin bar along the top while a page change is under way: it starts when you click a link inside the app
 * and finishes when the address has changed. Nothing to wire into individual links.
 */
function Bar() {
  const pathname = usePathname();
  const search = useSearchParams();
  const [state, setState] = useState<"idle" | "running" | "done">("idle");
  // The address it last saw: when it changes, a page change under way is done.
  const where = `${pathname}?${search.toString()}`;
  const [seen, setSeen] = useState(where);
  if (where !== seen) {
    setSeen(where);
    if (state === "running") setState("done");
  }

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, location.href);
      if (url.origin !== location.origin || url.pathname + url.search === location.pathname + location.search) return;
      setState("running");
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  useEffect(() => {
    if (state !== "done") return;
    const fade = setTimeout(() => setState("idle"), 400);
    return () => clearTimeout(fade);
  }, [state]);

  return <div className="syruss-progress" data-state={state} role="progressbar" aria-hidden={state === "idle"} />;
}

export function NavigationProgress() {
  return (
    <Suspense fallback={null}>
      <Bar />
    </Suspense>
  );
}
