"use client";

import { Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

/** Dark is the default; this switches to light and remembers it on this device. */
export function ThemeToggle() {
  // The page's own theme attribute is the one source: this follows it (the server renders dark, the default)
  const theme = useSyncExternalStore(
    (onChange) => {
      const watch = new MutationObserver(onChange);
      watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      return () => watch.disconnect();
    },
    () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"),
    () => "dark" as const,
  );
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("syruss-theme", next);
    } catch {
      /* private mode: the choice just lasts for this visit */
    }
  };
  return (
    <button type="button" className="theme-toggle" onClick={flip} aria-label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}>
      {theme === "dark" ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
    </button>
  );
}
