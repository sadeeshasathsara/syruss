"use client";

import { useEffect } from "react";

/** After Keycloak signs someone out, end this app's own session too. */
export function ClearSession() {
  useEffect(() => {
    void fetch("/api/auth/clear", { method: "POST" });
  }, []);
  return null;
}
