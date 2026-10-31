"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

/** Dark is the default; this switches to light and remembers it on this device. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
  }, []);
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
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
