"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Sigma, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useShell } from "@/components/shell/ShellContext";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button, IconButton } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { ApiError, api } from "@/lib/api";
import type { Screen, ScreenAggregate, ScreenCatalogue, ScreenColumn, ScreenDefinition, ScreenFormat } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { BetaTag } from "./BetaTag";
import { AGGREGATE_CHOICES, FORMAT_CHOICES } from "./format";
import { FilterEditor, toRequest } from "./QuickFilters";
import { ScreenTable } from "./ScreenTable";

const slug = (name: string) => {
  const s = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "field";
  return /^[a-z]/.test(s) ? s : `f_${s}`;
};

/** A short, unused name for a column (formulas refer to it), made from its label. */
function columnId(label: string, taken: string[], reserved: Set<string>): string {
  const base = slug(label).slice(0, 30);
  let id = reserved.has(base) ? `${base}_col` : base;
  let n = 2;
  while (taken.includes(id)) id = `${base}_${n++}`;
  return id;
}

function problemsOf(error: unknown): string[] {
  if (!(error instanceof ApiError)) return error instanceof Error ? [error.message] : [];
  const d = error.detail as { problems?: string[]; message?: string } | string | undefined;
  if (d && typeof d === "object" && d.problems?.length) return d.problems;
  return [error.message];
}

/**
 * The screen builder. Choose what the rows are, add columns (a field of the template, or a calculation), filter, sort and
 * group, and watch the table take shape beside it. The same checks the server makes on saving run on every change.
 */
export function ScreenBuilder({ initial }: { initial?: Screen }) {
  const { workspace } = useWorkspace();
  const wid = workspace.id;
  const catalogue = useQuery({ queryKey: ["screen-catalogue", wid], queryFn: () => api.screens.catalogue(wid) });
  const starters = useQuery({ queryKey: ["screen-starters", wid], queryFn: () => api.screens.starters(wid) });
  if (catalogue.isError || starters.isError) return <ErrorNote message="Couldn't prepare the builder." onRetry={() => { catalogue.refetch(); starters.refetch(); }} />;
  if (!catalogue.data || !starters.data) return <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-12 w-full" />)}</div>;
  return <BuilderForm catalogue={catalogue.data} starters={starters.data} initial={initial} />;
}

function BuilderForm({ catalogue, starters, initial }: { catalogue: ScreenCatalogue; starters: { name: string; definition: ScreenDefinition }[]; initial?: Screen }) {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const router = useRouter();
  const queryClient = useQueryClient();
  const { announce } = useShell();

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [def, setDef] = useState<ScreenDefinition>(
    () => initial?.definition ?? starters[0]?.definition ?? { source: { object: catalogue.objects.find((o) => o.multiple)?.type ?? catalogue.objects[0]?.type ?? "" }, columns: [] },
  );
  // What is previewed and saved leaves out filters still being filled in, and writes "is one of" as a list.
  const clean: ScreenDefinition = useMemo(() => ({ ...def, filters: toRequest(def.filters ?? []) }), [def]);
  const [debounced, setDebounced] = useState(clean);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(clean), 500);
    return () => clearTimeout(t);
  }, [clean]);

  const object = catalogue.objects.find((o) => o.type === def.source.object);
  const reserved = useMemo(() => new Set(catalogue.functions.map((f) => f.name)), [catalogue.functions]);
  const fieldChoices = useMemo(() => {
    const own = (object?.fields ?? []).map((f) => ({ value: f.name, label: f.name, type: f.type }));
    const doc = catalogue.objects.filter((o) => !o.multiple && o.type !== object?.type)
      .flatMap((o) => o.fields.map((f) => ({ value: `${o.type}.${f.name}`, label: `${o.type}: ${f.name}`, type: f.type })));
    return [...own, ...doc];
  }, [catalogue.objects, object]);
  const variables = useMemo(() => {
    const own = (object?.fields ?? []).map((f) => f.variable);
    const doc = catalogue.objects.filter((o) => !o.multiple && o.type !== object?.type).flatMap((o) => o.fields.map((f) => `${slug(o.type)}__${f.variable}`));
    return [...own, ...doc];
  }, [catalogue.objects, object]);
  const ids = def.columns.map((c) => c.id);

  const patch = (next: Partial<ScreenDefinition>) => setDef((d) => ({ ...d, ...next }));
  const setColumn = (i: number, change: Partial<ScreenColumn>) => patch({ columns: def.columns.map((c, n) => (n === i ? { ...c, ...change } : c)) });
  const addField = () => {
    const used = new Set(def.columns.map((c) => c.field));
    const pick = fieldChoices.find((f) => !used.has(f.value)) ?? fieldChoices[0];
    if (!pick) return;
    const label = pick.value.split(".").pop()!;
    patch({ columns: [...def.columns, { id: columnId(label, ids, reserved), label: label.replace(/_/g, " "), kind: "field", field: pick.value,
      format: pick.type === "number" || pick.type === "integer" ? { type: "number" } : pick.type === "date" ? { type: "date" } : undefined }] });
  };
  const addFormula = () => patch({ columns: [...def.columns, { id: columnId("calculated", ids, reserved), label: "Calculated", kind: "formula", formula: "" }] });
  const removeColumn = (i: number) => {
    const gone = def.columns[i].id;
    patch({
      columns: def.columns.filter((_, n) => n !== i),
      group_by: (def.group_by ?? []).filter((g) => g !== gone),
      sort: (def.sort ?? []).filter((s) => s.column !== gone),
      filters: (def.filters ?? []).filter((f) => f.field !== gone),
    });
  };
  const move = (i: number, by: -1 | 1) => {
    const j = i + by;
    if (j < 0 || j >= def.columns.length) return;
    const next = [...def.columns];
    [next[i], next[j]] = [next[j], next[i]];
    patch({ columns: next });
  };
  const chooseObject = (type: string) => {
    const starter = starters.find((s) => s.definition.source.object === type);
    setDef({ source: { object: type }, columns: starter?.definition.columns ?? [] });
  };

  const preview = useQuery({
    queryKey: ["screen-preview", wid, JSON.stringify(debounced)],
    queryFn: () => api.screens.preview(wid, debounced),
    enabled: debounced.columns.length > 0,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const save = useMutation({
    mutationFn: () => (initial
      ? api.screens.update(wid, initial.slug, { name, description, definition: clean })
      : api.screens.create(wid, { name, description, definition: clean })),
    onSuccess: (saved) => {
      ["screens", "screen", "screen-run"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
      announce(initial ? `You changed the screen ${saved.name}` : `You built the screen ${saved.name}`);
      router.push(href(`analytics/${saved.slug}`));
    },
  });
  const problems = preview.error ? problemsOf(preview.error) : [];
  const grouped = (def.group_by?.length ?? 0) > 0;
  const columnChoices = def.columns.map((c) => ({ value: c.id, label: c.label }));
  const filterChoices = [...columnChoices, ...(object?.fields ?? []).filter((f) => !ids.includes(f.variable)).map((f) => ({ value: f.name, label: `${f.name} (field)` }))];
  const canSave = name.trim().length > 0 && def.columns.length > 0 && !preview.isError && !save.isPending;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={<>{initial ? `Edit ${initial.name}` : "New analytics screen"}<BetaTag /></>}
        back={{ href: initial ? href(`analytics/${initial.slug}`) : href("analytics"), label: initial ? initial.name : "Your screens" }}
        actions={<Button variant="primary" disabled={!canSave} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : initial ? "Save changes" : "Save screen"}</Button>}
      />
      {save.error && (
        <div role="alert" className="rounded-card border border-border bg-[var(--pill-negative-bg)] p-3 text-body text-[var(--pill-negative-ink)]">
          {problemsOf(save.error).map((p) => <p key={p}>{p}</p>)}
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-5 @5xl:grid-cols-[minmax(0,34rem)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Panel title="About this screen">
            <div className="flex flex-col gap-3">
              <Field label="Name">{(id) => <Input id={id} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="Overtime by guard" />}</Field>
              <Field label="What it is for (optional)">{(id) => <Textarea id={id} rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
              <Field label="Rows are" help={initial ? "Fixed once a screen is saved; build a new one for different rows." : "Each row of this kind, from every saved document."}>
                {(id) => (
                  <Select id={id} value={def.source.object} disabled={!!initial} onChange={chooseObject}
                    options={catalogue.objects.map((o) => ({ value: o.type, label: o.multiple ? o.type : `${o.type} (one per document)` }))} />
                )}
              </Field>
              <div className="flex items-center justify-between gap-3">
                <span className="text-body text-ink">Include records still waiting for review</span>
                <Switch label="Include records still waiting for review" checked={!!def.source.include_pending_review} onChange={(v) => patch({ source: { ...def.source, include_pending_review: v } })} />
              </div>
            </div>
          </Panel>

          <Panel title="Columns" aside={<span className="text-meta">{def.columns.length} of 30</span>}>
            <div className="flex flex-col gap-3">
              {def.columns.length === 0 && <p className="text-body text-muted">Add a column to begin: a field from your template, or a calculation.</p>}
              {def.columns.map((c, i) => (
                <ColumnRow
                  key={c.id} column={c} first={i === 0} last={i === def.columns.length - 1} fieldChoices={fieldChoices}
                  names={[...ids.filter((x) => x !== c.id), ...variables.filter((v) => !ids.includes(v))]} grouped={grouped}
                  onChange={(change) => setColumn(i, change)} onRemove={() => removeColumn(i)} onMove={(by) => move(i, by)}
                />
              ))}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="secondary" disabled={def.columns.length >= 30} onClick={addField}><Plus /> Add a field</Button>
                <Button size="sm" variant="secondary" disabled={def.columns.length >= 30} onClick={addFormula}><Sigma /> Add a calculation</Button>
              </div>
            </div>
          </Panel>

          <Panel title="Show only">
            <FilterEditor filters={def.filters ?? []} onChange={(filters) => patch({ filters })} columns={filterChoices} label="Add a filter" empty="Every row is shown." />
          </Panel>

          <Panel title="Order and groups">
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-16 text-body text-ink">Order by</span>
                <Select value={def.sort?.[0]?.column ?? "_"} label="Order by" className="w-auto min-w-36"
                  onChange={(v) => patch({ sort: v === "_" ? [] : [{ column: v, dir: def.sort?.[0]?.dir ?? "asc" }] })}
                  options={[{ value: "_", label: "As saved (newest first)" }, ...columnChoices]} />
                {def.sort?.[0] && (
                  <Select value={def.sort[0].dir} label="Direction" className="w-auto min-w-32"
                    onChange={(dir) => patch({ sort: [{ column: def.sort![0].column, dir }] })}
                    options={[{ value: "asc", label: "Lowest first" }, { value: "desc", label: "Highest first" }]} />
                )}
              </div>
              <fieldset className="flex flex-col gap-1.5">
                <legend className="mb-1 text-body text-ink">Group rows by</legend>
                {def.columns.length === 0 && <p className="text-body text-muted">Add columns first.</p>}
                <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                  {def.columns.map((c) => (
                    <label key={c.id} className="inline-flex items-center gap-2 text-body text-ink">
                      <input type="checkbox" checked={(def.group_by ?? []).includes(c.id)}
                        onChange={(e) => patch({ group_by: e.target.checked ? [...(def.group_by ?? []), c.id] : (def.group_by ?? []).filter((g) => g !== c.id) })} />
                      {c.label}
                    </label>
                  ))}
                </div>
                {grouped && <p className="text-meta text-muted">Each group becomes one row. Give every other column a total (sum, average, count...) or hide it.</p>}
              </fieldset>
              <div className="flex items-center justify-between gap-3">
                <span className="text-body text-ink">Show a totals row</span>
                <Switch label="Show a totals row" checked={!!def.totals} onChange={(v) => patch({ totals: v })} />
              </div>
            </div>
          </Panel>

          <Panel title="Calculations you can write">
            <p className="text-body text-muted">Use column names in a formula, for example <code className="rounded-chip bg-raised px-1">hours * rate</code>. Blank cells stay blank; anything that can&apos;t be worked out shows blank and is counted.</p>
            <ul className="mt-2 grid grid-cols-1 gap-x-4 gap-y-0.5 text-meta text-muted @2xl:grid-cols-2">
              {catalogue.functions.map((f) => <li key={f.name}><code className="text-ink">{f.usage}</code></li>)}
            </ul>
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-3 @5xl:sticky @5xl:top-2">
          <h2 className="text-section font-semibold text-ink">Preview</h2>
          {problems.length > 0 && (
            <div role="status" className="rounded-card border border-border bg-[var(--pill-warning-bg)] p-3 text-body text-[var(--pill-warning-ink)]">
              {problems.map((p) => <p key={p}>{p}</p>)}
            </div>
          )}
          {debounced.columns.length === 0 && <p className="text-body text-muted">Add a column and the table appears here.</p>}
          {preview.isFetching && !preview.data && <div className="flex flex-col gap-2">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-8 w-full" />)}</div>}
          {preview.data && !preview.isError && (
            <>
              <ScreenTable run={preview.data} maxHeight="calc(100dvh - 260px)" />
              <p className="text-meta text-muted">
                First {preview.data.rows.length} of {preview.data.total_rows.toLocaleString()} {preview.data.grouped ? "groups" : "rows"}, from {preview.data.documents_searched.toLocaleString()} documents.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function ColumnRow({ column, first, last, fieldChoices, names, grouped, onChange, onRemove, onMove }: {
  column: ScreenColumn;
  first: boolean;
  last: boolean;
  fieldChoices: { value: string; label: string }[];
  names: string[];
  grouped: boolean;
  onChange: (change: Partial<ScreenColumn>) => void;
  onRemove: () => void;
  onMove: (by: -1 | 1) => void;
}) {
  const format: ScreenFormat = { type: "text", ...column.format };
  const hasDecimals = ["number", "currency", "percent"].includes(format.type);
  return (
    <div className="flex flex-col gap-2 rounded-card border border-border bg-raised/40 p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <Input aria-label="Column title" value={column.label} maxLength={80} onChange={(e) => onChange({ label: e.target.value })} />
        </div>
        <IconButton aria-label="Move up" disabled={first} onClick={() => onMove(-1)}><ArrowUp /></IconButton>
        <IconButton aria-label="Move down" disabled={last} onClick={() => onMove(1)}><ArrowDown /></IconButton>
        <IconButton aria-label={`Remove ${column.label}`} onClick={onRemove}><Trash2 /></IconButton>
      </div>
      {column.kind === "field" ? (
        <Select value={column.field || fieldChoices[0]?.value || "_"} label="Field" onChange={(field) => onChange({ field })} options={fieldChoices.length ? fieldChoices : [{ value: "_", label: "No fields" }]} />
      ) : (
        <div className="flex flex-col gap-1.5">
          <Input aria-label="Formula" className="font-mono" value={column.formula ?? ""} maxLength={400} placeholder="for example: hours_between(in_time, out_time)" onChange={(e) => onChange({ formula: e.target.value })} />
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-meta text-muted">Use:</span>
            {names.slice(0, 14).map((n) => (
              <button key={n} type="button" onClick={() => onChange({ formula: `${column.formula ?? ""}${column.formula && !/[\s(,]$/.test(column.formula) ? " " : ""}${n}` })}
                className="rounded-chip bg-raised px-1.5 text-meta text-ink hover:bg-active">{n}</button>
            ))}
          </div>
          <label className="inline-flex items-center gap-2 text-meta text-muted">
            <input type="checkbox" checked={column.scope === "group"} disabled={!grouped} onChange={(e) => onChange({ scope: e.target.checked ? "group" : "row" })} />
            Calculate from each group&apos;s totals{!grouped ? " (needs grouping)" : ""}
          </label>
          <p className="text-meta text-faint">Name in formulas: <code>{column.id}</code></p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={format.type} label="Show as" className="w-auto min-w-32" onChange={(type) => onChange({ format: { ...format, type } })} options={FORMAT_CHOICES} />
        {hasDecimals && (
          <Input aria-label="Decimal places" type="number" min={0} max={6} className="w-20" value={format.decimals ?? ""} placeholder="Decimals"
            onChange={(e) => onChange({ format: { ...format, decimals: e.target.value === "" ? undefined : Math.max(0, Math.min(6, Number(e.target.value))) } })} />
        )}
        {format.type === "currency" && (
          <Input aria-label="Currency code" className="w-20" value={format.currency ?? ""} placeholder="USD" maxLength={8} onChange={(e) => onChange({ format: { ...format, currency: e.target.value.toUpperCase() || undefined } })} />
        )}
        <Select value={(column.aggregate ?? "none") as ScreenAggregate | "none"} label="Total" className="w-auto min-w-32"
          onChange={(v) => onChange({ aggregate: v === "none" ? undefined : v })} options={AGGREGATE_CHOICES} />
        <label className="inline-flex items-center gap-2 text-meta text-muted">
          <input type="checkbox" checked={!!column.hidden} onChange={(e) => onChange({ hidden: e.target.checked })} /> Hide
        </label>
      </div>
    </div>
  );
}
