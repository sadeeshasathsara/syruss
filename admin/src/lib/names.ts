"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Names } from "./types";

const EMPTY: Names = { organizations: {}, workspaces: {} };

/**
 * The organization and workspace names a live page has met: from the responses it already has (`learn`), and asked
 * for in one batch when an event names one it hasn't seen (`need`).
 */
export function useNames() {
  const [names, setNames] = useState<Names>(EMPTY);
  const known = useRef<Names>(EMPTY);
  const wanted = useRef({ tenants: new Set<string>(), workspaces: new Set<string>() });
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const learn = useCallback((more?: Partial<Names>) => {
    if (!more) return;
    known.current = {
      organizations: { ...known.current.organizations, ...more.organizations },
      workspaces: { ...known.current.workspaces, ...more.workspaces },
    };
    setNames(known.current);
  }, []);

  const need = useCallback((tenantId?: string, workspaceId?: string) => {
    if (tenantId && !known.current.organizations[tenantId]) wanted.current.tenants.add(tenantId);
    if (workspaceId && !known.current.workspaces[workspaceId]) wanted.current.workspaces.add(workspaceId);
    if (!wanted.current.tenants.size && !wanted.current.workspaces.size) return;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const tenants = [...wanted.current.tenants];
      const workspaces = [...wanted.current.workspaces];
      wanted.current = { tenants: new Set(), workspaces: new Set() };
      api.names(tenants, workspaces).then(learn).catch(() => undefined);
    }, 400);
  }, [learn]);

  useEffect(() => () => clearTimeout(timer.current), []);
  return { names, learn, need };
}
