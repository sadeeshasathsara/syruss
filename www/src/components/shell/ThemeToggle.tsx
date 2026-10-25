"use client";

import { Moon, Sun } from "lucide-react";
import { IconButton } from "@/components/ui/Button";
import { applyTheme, useTheme } from "@/lib/browser";

export function ThemeToggle() {
  const theme = useTheme();
  const toggle = () => applyTheme(theme === "dark" ? "light" : "dark");
  return (
    <IconButton aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"} onClick={toggle}>
      {theme === "dark" ? <Sun /> : <Moon />}
    </IconButton>
  );
}
