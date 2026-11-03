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
import type { TemplateSchema } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

type Part = { object: string; field: string };
const ADD = "add";
const same = (a: Part[], b: Part[]) => a.length === b.length && a.every((p, i) => p.object === b[i].object && p.field === b[i].field);

/**
 * How a document is named to people: which fields of the one-per-document objects make up its identification key
 * (a site and a date, say). The agent proposes it with the template; anyone who can publish the template can change it,
 * and every list, notification and log line follows at once.
 */
export function DocumentKeyEditor({ schema }: { schema: TemplateSchema }) {
  const { workspace } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const queryClient = useQueryClient();
  const saved = schema.identification?.parts ?? [];
  const savedSeparator = schema.identification?.separator ?? "_";
  const [parts, setParts] = useState<Part[]>(saved);
  const [separator, setSeparator] = useState(savedSeparator);
  const documents = useQuery({ queryKey: ["documents", workspace.id], queryFn: () => api.documents.list(workspace.id, undefined, 500) });
  const example = documents.data?.find((d) => d.display_name)?.display_name;

  const candidates = schema.objects
    .filter((o) => o.multiple === false)
    .flatMap((o) => o.fields.map((f) => ({ object: o.type, field: fieldName(f) })))
    .filter((c) => !parts.some((p) => p.object === c.object && p.field === c.field));
  const label = (p: Part) => `${humanize(p.object)} · ${humanize(p.field)}`;

  const save = useMutation({
    mutationFn: () => api.template.setIdentification(workspace.id, parts, separator),
    onSuccess: (template) => {
      queryClient.setQueryData(["template", workspace.id], template);
      ["documents", "template"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, workspace.id] }));
    },
  });
  const changed = !same(parts, saved) || (parts.length > 0 && separator !== savedSeparator);
  const move = (i: number, by: number) => setParts((cur) => {
    const next = [...cur];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    return next;
  });
  const editable = can("template.publish");

  return (
    <Panel title="How documents are named" aside={example ? <span className="num">e.g. {example}</span> : undefined}>
      <p className="mb-3 text-body text-muted">
        Lists, notifications and the log call a document by this key instead of its file name. Pick the details that tell one
        document from another, such as where and when. Only details that appear once per document can be used.
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
                <Button variant="ghost" size="sm" aria-label={`Remove ${label(p)}`} onClick={() => setParts((cur) => cur.filter((_, j) => j !== i))}><X /></Button>
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
                  setParts((cur) => [...cur, { object, field: rest.join("::") }]);
                }}
                options={[{ value: ADD, label: "Add a detail…" }, ...candidates.map((c) => ({ value: `${c.object}::${c.field}`, label: label(c) }))]}
              />
            </div>
          )}
          {parts.length > 1 && (
            <label className="flex items-center gap-2 text-body text-ink">
              Joined with
              <Input className="w-16 text-center" maxLength={5} value={separator} onChange={(e) => setSeparator(e.target.value)} aria-label="Separator between the details" />
            </label>
          )}
          <Button variant="primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>Save</Button>
          <SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess && !changed} />
        </div>
      )}
      {candidates.length === 0 && parts.length === 0 && (
        <p className="mt-3 text-body text-muted">This template has no details that appear once per document. Refine it with new samples to add some.</p>
      )}
    </Panel>
  );
}
