"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Lock } from "lucide-react";
import { api } from "@/lib/api";
import type { Permission, WorkspaceSettingsData } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

/** The title of one settings page (a sub tab). */
export function SettingsHeader({ title, description, actions }: { title: string; description: string; actions?: React.ReactNode }) {
  return (
    <header className="flex flex-col gap-3 border-b border-border pb-5 @3xl:flex-row @3xl:items-end @3xl:justify-between">
      <div className="min-w-0 max-w-2xl">
        <h1 className="text-title font-semibold tracking-[-0.01em] text-ink">{title}</h1>
        <p className="mt-1 text-body text-muted">{description}</p>
      </div>
      {actions && <div className="flex flex-none flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** One setting group: what it is on the left, its controls on the right. */
export function Section({ title, description, children }: { title: string; description?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="grid grid-cols-1 gap-4 border-b border-border py-6 last:border-0 @3xl:grid-cols-[260px_minmax(0,1fr)] @3xl:gap-10">
      <div>
        <h2 className="text-section font-semibold text-ink">{title}</h2>
        {description && <p className="mt-1 text-body text-muted">{description}</p>}
      </div>
      <div className="min-w-0 max-w-2xl">{children}</div>
    </section>
  );
}

/** A row in a settings panel: the label and its explanation, the control on the right. */
export function SettingRow({ label, help, control, htmlFor }: { label: string; help?: string; control: React.ReactNode; htmlFor?: string }) {
  return (
    <div className="flex items-start justify-between gap-6 border-b border-rule py-3 first:pt-0 last:border-0 last:pb-0">
      <div className="min-w-0">
        <label htmlFor={htmlFor} className="text-body font-medium text-ink">{label}</label>
        {help && <p className="mt-0.5 text-body text-muted">{help}</p>}
      </div>
      <div className="flex-none pt-0.5">{control}</div>
    </div>
  );
}

/** The workspace's settings and your access to them, with a save that reports how it went. */
export function useWorkspaceSettings() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["settings", workspace.id], queryFn: () => api.settings.get(workspace.id) });
  const save = useMutation({
    mutationFn: (patch: Partial<{ [K in keyof WorkspaceSettingsData]: Partial<WorkspaceSettingsData[K]> }>) => api.settings.update(workspace.id, patch),
    onSuccess: (data) => queryClient.setQueryData(["settings", workspace.id], data),
  });
  const can = (p: Permission) => !!query.data?.access.permissions.includes(p);
  return { settings: query.data?.settings, access: query.data?.access, query, save, can };
}

/** Shown in place of controls your role doesn't cover. */
export function NoAccess({ what, role }: { what: string; role?: string }) {
  return (
    <p className="flex items-start gap-2 rounded-card bg-raised px-4 py-3 text-body text-muted">
      <Lock className="mt-0.5 size-4 flex-none" />
      <span>
        Your role{role ? ` (${role})` : ""} can see this but not {what}. Ask an owner or admin if you need to.
      </span>
    </p>
  );
}

export function SavedNote({ pending, error, saved }: { pending: boolean; error: Error | null; saved: boolean }) {
  if (pending) return <span className="text-body text-muted">Saving…</span>;
  if (error) return <span className="text-body text-negative">{error.message}</span>;
  if (saved) return <span className="flex items-center gap-1 text-body text-muted"><Check className="size-4" /> Saved</span>;
  return null;
}

export { ShowOnce } from "@/components/ui/ShowOnce";
