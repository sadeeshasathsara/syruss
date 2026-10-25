"use client";

import { createContext, useContext } from "react";
import type { Workspace } from "./types";

interface WorkspaceContextValue {
  org: string;
  workspace: Workspace;
  /** Link inside this workspace: href("documents/123") → /default/w/invoices/documents/123 */
  href: (path?: string) => string;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ org, workspace, children }: { org: string; workspace: Workspace; children: React.ReactNode }) {
  const base = `/${org}/w/${workspace.slug}`;
  const href = (path?: string) => (path ? `${base}/${path.replace(/^\//, "")}` : base);
  return <WorkspaceContext.Provider value={{ org, workspace, href }}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside a workspace route");
  return ctx;
}
