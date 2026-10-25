"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Field";
import { Switch } from "@/components/ui/Switch";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { NoAccess, SavedNote, Section, SettingRow, SettingsHeader, useWorkspaceSettings } from "./kit";

const FILE_TYPES: { type: string; label: string }[] = [
  { type: "image/jpeg", label: "JPEG" },
  { type: "image/png", label: "PNG" },
  { type: "image/webp", label: "WebP" },
  { type: "image/tiff", label: "TIFF" },
];

function Loading() {
  return <div className="flex flex-col gap-3 pt-6">{[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>;
}

export function AutonomySettings() {
  const { settings, access, save, can } = useWorkspaceSettings();
  const editable = can("workspace.manage");
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Autonomy" description="What the agent may do on its own. The platform enforces these; the agent is told them every time it starts work." />
      {!settings ? <Loading /> : (
        <>
          {!editable && access && <div className="pt-5"><NoAccess what="change the agent's autonomy" role={access.role_label} /></div>}
          <Section title="Learning" description="What the agent adds to the workspace's knowledge without asking.">
            <div className="rounded-card border border-border bg-card p-4">
              <SettingRow
                label="Register new people and things"
                help="From committed records or your answers. Off: it asks you first."
                control={<Switch label="Register new people and things" disabled={!editable} checked={settings.agent.may_register_entities} onChange={(v) => save.mutate({ agent: { may_register_entities: v } })} />}
              />
              <SettingRow
                label="Learn other spellings"
                help="Nicknames, abbreviations and handwriting it has confirmed. Off: it asks you first."
                control={<Switch label="Learn other spellings" disabled={!editable} checked={settings.agent.may_learn_aliases} onChange={(v) => save.mutate({ agent: { may_learn_aliases: v } })} />}
              />
            </div>
            <div className="mt-2"><SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess} /></div>
          </Section>
          <Section title="Reading pages" description="Every document read is saved in the database, so Syruss answers from the records. Looking at a page again costs much more; these are hard limits.">
            <div className="rounded-card border border-border bg-card p-4">
              {([
                ["pages_per_chat_request", "Pages per chat message", "For questions the records can't answer.", 20],
                ["pages_per_person_per_day", "Pages per person per day", "Across all of one person's chats.", 500],
                ["past_pages_per_run", "Already-read pages while working", "While reading a new document, how many old ones it may look at again.", 20],
              ] as const).map(([key, label, help, max]) => (
                <SettingRow
                  key={key}
                  label={label}
                  help={help}
                  htmlFor={key}
                  control={
                    <Input
                      id={key}
                      type="number"
                      min={0}
                      max={max}
                      className="w-20 text-right"
                      disabled={!editable}
                      defaultValue={settings.agent[key]}
                      onBlur={(e) => {
                        const value = Math.max(0, Math.min(max, Math.round(Number(e.target.value))));
                        if (Number.isFinite(value) && value !== settings.agent[key]) save.mutate({ agent: { [key]: value } });
                      }}
                    />
                  }
                />
              ))}
            </div>
          </Section>
          <Section title="Always needs you" description="Never done on its own, whatever these settings say.">
            <ul className="flex list-disc flex-col gap-1 pl-5 text-body text-ink">
              <li>Changing the template</li>
              <li>Merging people or things it thinks are the same</li>
              <li>Importing a data source</li>
            </ul>
          </Section>
        </>
      )}
    </div>
  );
}

export function InstructionsSettings() {
  const { settings, access, save, can } = useWorkspaceSettings();
  const editable = can("workspace.manage");
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? settings?.agent.instructions ?? "";
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Instructions" description="Standing instructions the agent reads every time it starts work in this workspace." />
      {!settings ? <Loading /> : (
        <Section title="For the agent" description="How your business works, what to watch for, how to write values. Keep it short and specific.">
          {!editable && access && <div className="mb-4"><NoAccess what="change the instructions" role={access.role_label} /></div>}
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate({ agent: { instructions: value } }, { onSuccess: () => setDraft(null) });
            }}
          >
            <Textarea
              aria-label="Instructions for the agent"
              value={value}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={4000}
              disabled={!editable}
              className="min-h-48"
              placeholder={"For example:\n- Site codes start with S and three digits.\n- Night shifts run from 19:00 to 07:00."}
            />
            <div className="flex items-center gap-3">
              <Button type="submit" variant="primary" disabled={!editable || draft === null || save.isPending}>Save instructions</Button>
              <span className="num text-meta text-muted">{value.length} / 4000</span>
              <SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess && draft === null} />
            </div>
          </form>
        </Section>
      )}
    </div>
  );
}

export function IntakeSettings() {
  const { settings, access, save, can } = useWorkspaceSettings();
  const editable = can("workspace.manage");
  const [size, setSize] = useState<string | null>(null);
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Intake" description="What happens to documents when they're uploaded, and which files are accepted." />
      {!settings ? <Loading /> : (
        <>
          {!editable && access && <div className="pt-5"><NoAccess what="change intake" role={access.role_label} /></div>}
          <Section title="Processing">
            <div className="rounded-card border border-border bg-card p-4">
              <SettingRow
                label="Read documents as soon as they're uploaded"
                help="Off: uploads wait until someone starts them."
                control={<Switch label="Read documents as soon as they're uploaded" disabled={!editable} checked={settings.documents.auto_process} onChange={(v) => save.mutate({ documents: { auto_process: v } })} />}
              />
            </div>
          </Section>
          <Section title="Files" description="Uploads outside these are refused with the reason.">
            <div className="flex flex-col gap-4 rounded-card border border-border bg-card p-4">
              <fieldset>
                <legend className="text-body font-medium text-ink">Accepted types</legend>
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
                  {FILE_TYPES.map((t) => {
                    const on = settings.documents.allowed_types.includes(t.type);
                    const only = on && settings.documents.allowed_types.length === 1;
                    return (
                      <label key={t.type} className="flex items-center gap-2 text-body text-ink">
                        <input
                          type="checkbox"
                          className="size-4 accent-[var(--ink)]"
                          checked={on}
                          disabled={!editable || only}
                          onChange={(e) =>
                            save.mutate({
                              documents: {
                                allowed_types: e.target.checked
                                  ? [...settings.documents.allowed_types, t.type]
                                  : settings.documents.allowed_types.filter((x) => x !== t.type),
                              },
                            })
                          }
                        />
                        {t.label}
                      </label>
                    );
                  })}
                </div>
              </fieldset>
              <form
                className="flex flex-wrap items-end gap-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  const mb = Number(size);
                  if (mb >= 1 && mb <= 50) save.mutate({ documents: { max_file_mb: mb } }, { onSuccess: () => setSize(null) });
                }}
              >
                <label className="flex flex-col gap-2">
                  <span className="text-body font-medium text-ink">Largest file (MB)</span>
                  <Input type="number" min={1} max={50} className="w-28" disabled={!editable} value={size ?? String(settings.documents.max_file_mb)} onChange={(e) => setSize(e.target.value)} />
                </label>
                <Button type="submit" variant="secondary" disabled={!editable || size === null}>Save</Button>
              </form>
            </div>
            <div className="mt-2"><SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess} /></div>
          </Section>
        </>
      )}
    </div>
  );
}

export function ReviewRuleSettings() {
  const { settings, access, save, can } = useWorkspaceSettings();
  const editable = can("workspace.manage");
  const [threshold, setThreshold] = useState<number | null>(null);
  const value = threshold ?? Math.round((settings?.documents.commit_threshold ?? 0.8) * 100);
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Review rules" description="When the agent may save a record on its own, and when a person must look first." />
      {!settings ? <Loading /> : (
        <Section title="Confidence to save on its own" description="Below this, the agent must send the document to review. The platform refuses anything else.">
          {!editable && access && <div className="mb-4"><NoAccess what="change review rules" role={access.role_label} /></div>}
          <div className="flex flex-col gap-4 rounded-card border border-border bg-card p-4">
            <div className="flex items-center gap-4">
              <input
                type="range"
                min={50}
                max={100}
                step={1}
                value={value}
                disabled={!editable}
                onChange={(e) => setThreshold(Number(e.target.value))}
                aria-label="Confidence needed to save on its own"
                className="h-1.5 flex-1 accent-[var(--ink)]"
              />
              <span className="num w-12 text-right text-section font-semibold text-ink">{value}%</span>
            </div>
            <p className="text-body text-muted">
              {value >= 95
                ? "Strict: almost everything handwritten will reach review."
                : value >= 80
                  ? "Balanced: the agent saves what it has confirmed against known people and past records."
                  : "Lenient: more is saved without a person looking. Check the review history regularly."}
            </p>
            <div className="flex items-center gap-3">
              <Button
                variant="primary"
                disabled={!editable || threshold === null || save.isPending}
                onClick={() => save.mutate({ documents: { commit_threshold: value / 100 } }, { onSuccess: () => setThreshold(null) })}
              >
                Save
              </Button>
              <SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess && threshold === null} />
            </div>
          </div>
        </Section>
      )}
    </div>
  );
}

export function NotificationSettings() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const prefs = useQuery({ queryKey: ["notification-prefs", workspace.id], queryFn: () => api.settings.myNotifications(workspace.id) });
  const save = useMutation({
    mutationFn: (next: Record<string, boolean>) => api.settings.setMyNotifications(workspace.id, next),
    onSuccess: (data) => queryClient.setQueryData(["notification-prefs", workspace.id], data),
  });
  const current = Object.fromEntries((prefs.data ?? []).map((p) => [p.type, p.enabled]));
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Your preferences" description="What you hear about from this workspace. These are yours; others choose their own." />
      {!prefs.data ? <Loading /> : (
        <Section title="In the app" description="Shown in the bell at the top, live. Kinds your role doesn't cover are off.">
          <div className="rounded-card border border-border bg-card p-4">
            {prefs.data.map((p) => (
              <SettingRow
                key={p.type}
                label={p.label}
                help={p.available ? undefined : "Not part of your role."}
                control={<Switch label={p.label} disabled={!p.available || save.isPending} checked={p.available && p.enabled} onChange={(v) => save.mutate({ ...current, [p.type]: v })} />}
              />
            ))}
          </div>
          <p className="mt-3 text-body text-muted">Email isn’t connected yet. Webhooks can forward the same events to your own systems (Integrations).</p>
          <div className="mt-2"><SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess} /></div>
        </Section>
      )}
    </div>
  );
}
