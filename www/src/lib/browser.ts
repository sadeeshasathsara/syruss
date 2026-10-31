"use client";

import { useSyncExternalStore } from "react";

/** A CSS media query as React state (false during server rendering). */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

const storageListeners = new Set<() => void>();

/** A localStorage value as React state. Per-viewer conveniences only; may be unavailable. */
export function useStoredValue(key: string): string | null {
  return useSyncExternalStore(
    (onChange) => {
      storageListeners.add(onChange);
      window.addEventListener("storage", onChange);
      return () => {
        storageListeners.delete(onChange);
        window.removeEventListener("storage", onChange);
      };
    },
    () => {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    () => null,
  );
}

export function storeValue(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the preference just isn't remembered */
  }
  storageListeners.forEach((l) => l());
}

/** The document's current theme, kept in sync with the html data attribute. */
/** Switches the theme now and remembers it on this device. */
export function applyTheme(next: "light" | "dark"): void {
  if (next === "dark") document.documentElement.dataset.theme = "dark";
  else delete document.documentElement.dataset.theme;
  storeValue("theme", next);
}

export function useTheme(): "light" | "dark" {
  return useSyncExternalStore(
    (onChange) => {
      const observer = new MutationObserver(onChange);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      return () => observer.disconnect();
    },
    () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"),
    () => "light",
  );
}
