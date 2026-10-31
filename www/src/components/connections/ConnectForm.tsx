"use client";

import { Field, Input, Textarea } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import type { DestinationField, DestinationKind } from "@/lib/types";

/** Whether a field applies given the other settings (an API key's name only matters when the sign-in is an API key). Pass the settings with defaults filled in. */
export function visible(f: DestinationField, config: Record<string, unknown>): boolean {
  return !f.show_if || Object.entries(f.show_if).every(([k, v]) => config[k] === v);
}

/** The settings with each field's default filled in, so what is shown is what is saved. */
export function withDefaults(kind: DestinationKind, config: Record<string, unknown>): Record<string, unknown> {
  const out = { ...config };
  for (const f of kind.fields) if (!f.secret && out[f.key] === undefined && f.default !== null && f.default !== undefined) out[f.key] = f.default;
  return out;
}

/** What is still missing before the connection can be tried, in the same words the server uses. */
export function missing(kind: DestinationKind, config: Record<string, unknown>, secrets: Record<string, string>, stored: string[] = []): string[] {
  const merged = withDefaults(kind, config);
  return kind.fields
    .filter((f) => f.required && visible(f, merged))
    .filter((f) => (f.secret ? !(secrets[f.key] || stored.includes(f.key)) : !merged[f.key]))
    .map((f) => f.label);
}

/** The form for connecting a kind of destination, built from the fields the server describes. Saved secrets are never shown. */
export function ConnectForm({
  kind, config, secrets, stored = [], onConfig, onSecrets,
}: {
  kind: DestinationKind;
  config: Record<string, unknown>;
  secrets: Record<string, string>;
  stored?: string[];
  onConfig: (next: Record<string, unknown>) => void;
  onSecrets: (next: Record<string, string>) => void;
}) {
  const merged = withDefaults(kind, config);
  return (
    <div className="flex flex-col gap-4">
      {kind.fields.filter((f) => visible(f, merged)).map((f) => (
        <Field key={f.key} label={f.required ? f.label : `${f.label} (optional)`} help={f.secret && stored.includes(f.key) ? "Saved. Leave empty to keep it, or type a new one to replace it." : f.help || undefined}>
          {(id) => {
            if (f.kind === "select") {
              return <Select id={id} value={String(merged[f.key] ?? f.options?.[0]?.value ?? "")} onChange={(v) => onConfig({ ...config, [f.key]: v })} options={f.options ?? []} />;
            }
            if (f.kind === "textarea") {
              const value = f.secret ? secrets[f.key] ?? "" : String(merged[f.key] ?? "");
              return <Textarea id={id} value={value} rows={3} placeholder={f.placeholder} className="font-mono" onChange={(e) => (f.secret ? onSecrets({ ...secrets, [f.key]: e.target.value }) : onConfig({ ...config, [f.key]: e.target.value }))} />;
            }
            if (f.kind === "checkbox") {
              return <input id={id} type="checkbox" className="size-4 accent-[var(--ink)]" checked={!!merged[f.key]} onChange={(e) => onConfig({ ...config, [f.key]: e.target.checked })} />;
            }
            const type = f.kind === "password" ? "password" : f.kind === "url" ? "url" : f.kind === "number" ? "number" : "text";
            return f.secret ? (
              <Input id={id} type={type} autoComplete="new-password" value={secrets[f.key] ?? ""} placeholder={stored.includes(f.key) ? "••••••••" : f.placeholder} onChange={(e) => onSecrets({ ...secrets, [f.key]: e.target.value })} />
            ) : (
              <Input id={id} type={type} value={String(merged[f.key] ?? "")} placeholder={f.placeholder} onChange={(e) => onConfig({ ...config, [f.key]: f.kind === "number" ? Number(e.target.value) : e.target.value })} />
            );
          }}
        </Field>
      ))}
    </div>
  );
}
