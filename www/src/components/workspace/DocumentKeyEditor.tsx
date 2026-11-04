"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useState } from "react";
import { SavedNote } from "@/components/settings/kit";
import { Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { fieldName, humanize } from "@/lib/format";
import type { DocumentIdentification, TemplateSchema } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

type Part = { object: string; field: string };
const ADD = "add";
const same = (a: Part[], b: Part[]) => a.length === b.length && a.every((p, i) => p.object === b[i].object && p.field === b[i].field);

/** The key's fields that still exist in the schema: removing a field, or an object turning into a repeating one, drops it. */
export function pruneIdentification(schema: TemplateSchema): TemplateSchema {
  const key = schema.identification;
  if (!key) return schema;
  const parts = key.parts.filter((p) =>
    schema.objects.some((o) => o.type === p.object && o.multiple === false && o.fields.some((f) => fieldName(f) === p.field)),
  );
  if (parts.length === key.parts.length) return schema;
  return { ...schema, identification: parts.length ? { ...key, parts } : undefined };
}

/**
 * The picker for a document's identification key: which fields of the one-per-document objects make it up (a site and
 * a date, say), in order, and what joins them. It only edits the key; saving is the caller's job.
 */
export function DocumentKeyFields({
  schema,
  onChange,
  editable = true,
  example,
}: {
  schema: TemplateSchema;
  onChange: (key: DocumentIdentification | undefined) => void;
  editable?: boolean;
  example?: string | null;
}) {
  const parts: Part[] = schema.identification?.parts ?? [];
  const separator = schema.identification?.separator ?? "_";
  const set = (next: Part[], sep = separator) => onChange(next.length ? { parts: next, separator: sep } : undefined);
  const candidates = schema.objects
    .filter((o) => o.multiple === false)
    .flatMap((o) => o.fields.map((f) => ({ object: o.type, field: fieldName(f) })))
    .filter((c) => !parts.some((p) => p.object === c.object && p.field === c.field));
  const label = (p: Part) => `${humanize(p.object)} · ${humanize(p.field)}`;
  const move = (i: number, by: number) => {
    const next = [...parts];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    set(next);
  };

  return (
    <div>
      <p className="mb-3 text-body text-muted">
        Lists, notifications and the log call a document by this key instead of its file name. Pick the details that tell one
        document from another, such as where and when. Only details that appear once per document can be used.
        {example ? <> Now: <span className="num text-ink">{example}</span></> : null}
      </p>
      {parts.length === 0 && <p className="mb-3 text-body text-ink">No key yet: documents are shown by their file name.</p>}
      <ol className="flex flex-col">
        {parts.map((p, i) => (
          <li key={`${p.object}.${p.field}`} className="flex items-center gap-2 border-t border-rule py-2">
            <span className="min-w-0 flex-1 truncate text-body text-ink">{label(p)}</span>
            {editable && (
              <>
                <Button variant="ghost" size="sm" aria-label={`Move ${label(p)} up`} disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp /></Button>
                <Button variant="ghost" size="sm" aria-label={`Move ${label(p)} down`} disabled={i === parts.length - 1} onClick={() => move(i, 1)}><ArrowDown /></Button>
                <Button variant="ghost" size="sm" aria-label={`Remove ${label(p)}`} onClick={() => set(parts.filter((_, j) => j !== i))}><X /></Button>
              </>
            )}
          </li>
        ))}
      </ol>
      {editable && (
        <div className="mt-3 flex flex-wrap items-end gap-3">
          {candidates.length > 0 && parts.length < 5 && (
            <div className="w-64">
              <Select
                label="Add a detail to the key"
                value={ADD}
                onChange={(v) => {
                  const [object, ...rest] = v.split("::");
                  set([...parts, { object, field: rest.join("::") }]);
                }}
                options={[{ value: ADD, label: "Add a detail…" }, ...candidates.map((c) => ({ value: `${c.object}::${c.field}`, label: label(c) }))]}
              />
            </div>
          )}
          {parts.length > 1 && (
            <label className="flex items-center gap-2 text-body text-ink">
              Joined with
              <Input className="w-16 text-center" maxLength={5} value={separator} onChange={(e) => set(parts, e.target.value)} aria-label="Separator between the details" />
            </label>
          )}
        </div>
      )}
      {candidates.length === 0 && parts.length === 0 && (
        <p className="mt-3 text-body text-muted">There are no details that appear once per document to build a key from.</p>
      )}
    </div>
  );
}

/** The published template's key, on the Template page: anyone who can publish the template can change it; everything follows at once. */
export function DocumentKeyEditor({ schema }: { schema: TemplateSchema }) {
  const { workspace } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<DocumentIdentification | undefined>(schema.identification);
  const documents = useQuery({ queryKey: ["documents", workspace.id], queryFn: () => api.documents.list(workspace.id, undefined, 500) });
  const example = documents.data?.find((d) => d.display_name)?.display_name;
  const saved = schema.identification;
  const save = useMutation({
    mutationFn: () => api.template.setIdentification(workspace.id, draft?.parts ?? [], draft?.separator ?? "_"),
    onSuccess: (template) => {
      queryClient.setQueryData(["template", workspace.id], template);
      ["documents", "template"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, workspace.id] }));
    },
  });
  const changed = !same(draft?.parts ?? [], saved?.parts ?? []) || (!!draft && draft.separator !== (saved?.separator ?? "_"));
  const editable = can("template.publish");

  return (
    <Panel title="How documents are named">
      <DocumentKeyFields schema={{ ...schema, identification: draft }} onChange={setDraft} editable={editable} example={example} />
      {editable && (
        <div className="mt-3 flex items-center gap-3">
          <Button variant="primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>Save</Button>
          <SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess && !changed} />
        </div>
      )}
    </Panel>
  );
}
