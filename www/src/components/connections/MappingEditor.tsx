"use client";

import { Plus, X } from "lucide-react";
import { Button, IconButton } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import type { DestinationKind, MappingField, MappingSources, MappingSpec } from "@/lib/types";

const SOURCE_OPTIONS = [
  { value: "field", label: "A field of the document" },
  { value: "formula", label: "A calculation" },
  { value: "constant", label: "A fixed value" },
] as const;

function fieldOptions(sources: MappingSources, only?: string) {
  const own = sources.objects.filter((o) => !only || o.type === only).flatMap((o) => o.fields.map((f) => ({ value: `${o.type}.${f.name}`, label: `${o.type} › ${f.name}` })));
  const doc = only ? [] : [{ value: "document.filename", label: "File name" }, { value: "document.external_id", label: "Your reference (external id)" }];
  return [{ value: "none", label: "Choose a field…" }, ...own, ...doc];
}

function ValueControl({ spec, sources, only, onChange }: { spec: MappingField; sources: MappingSources; only?: string; onChange: (next: MappingField) => void }) {
  if (spec.source === "field") {
    const options = fieldOptions(sources, only);
    const known = options.some((o) => o.value === spec.value);
    return <Select label={`Field for ${spec.target || "this value"}`} value={known && spec.value ? spec.value : "none"} onChange={(v) => onChange({ ...spec, value: v === "none" ? "" : v })} options={options} />;
  }
  return (
    <Input
      aria-label={spec.source === "formula" ? `Calculation for ${spec.target || "this value"}` : `Value for ${spec.target || "this value"}`}
      value={spec.value} onChange={(e) => onChange({ ...spec, value: e.target.value })}
      placeholder={spec.source === "formula" ? "round(Invoice_total * 100)" : "EUR"} className={spec.source === "formula" ? "font-mono" : undefined}
    />
  );
}

interface RowsProps {
  rows: MappingField[];
  sources: MappingSources;
  only?: string;
  fixed?: { key: string; label: string; required: boolean }[];   // the destination's own fields: rows are fixed, not typed
  onChange: (rows: MappingField[]) => void;
  addLabel: string;
}

function Rows({ rows, sources, only, fixed, onChange, addLabel }: RowsProps) {
  const set = (i: number, next: MappingField) => onChange(rows.map((r, j) => (j === i ? next : r)));
  const shown = fixed ? fixed.map((f) => ({ f, index: rows.findIndex((r) => r.target === f.key) })) : rows.map((_, index) => ({ f: null, index }));
  return (
    <div className="flex flex-col gap-2">
      {shown.map(({ f, index }, n) => {
        const spec: MappingField = index >= 0 ? rows[index] : { target: f!.key, source: "field", value: "" };
        const update = (next: MappingField) => (index >= 0 ? set(index, next) : onChange([...rows, next]));
        return (
          <div key={f?.key ?? n} className="grid grid-cols-1 gap-2 rounded-card border border-border bg-card p-3 @2xl:grid-cols-[minmax(0,1fr)_180px_minmax(0,1.3fr)_auto] @2xl:items-center @2xl:border-0 @2xl:bg-transparent @2xl:p-0">
            {f ? (
              <span className="text-body font-medium text-ink">{f.label}{f.required && <span className="ml-1 text-negative" title="Needed">*</span>}</span>
            ) : (
              <Input aria-label="Name of the field they receive" value={spec.target} onChange={(e) => update({ ...spec, target: e.target.value })} placeholder="invoice_number or customer.name" className="font-mono" />
            )}
            <Select label="Where it comes from" value={spec.source} onChange={(v) => update({ ...spec, source: v, value: "" })} options={[...SOURCE_OPTIONS]} />
            <ValueControl spec={spec} sources={sources} only={only} onChange={update} />
            {f ? (
              <span className="hidden w-8 @2xl:block" />
            ) : (
              <IconButton aria-label="Remove this field" onClick={() => onChange(rows.filter((_, j) => j !== index))}><X /></IconButton>
            )}
          </div>
        );
      })}
      {!fixed && <div><Button variant="secondary" size="sm" onClick={() => onChange([...rows, { target: "", source: "field", value: "" }])}><Plus /> {addLabel}</Button></div>}
    </div>
  );
}

/** Chooses what a destination receives: the record as it is, or fields filled from the record, a calculation or a fixed value. */
export function MappingEditor({ kind, sources, value, onChange }: { kind: DestinationKind; sources: MappingSources; value: MappingSpec; onChange: (next: MappingSpec) => void }) {
  const fixedTargets = kind.targets.filter((t) => !t.line);
  const lineTargets = kind.targets.filter((t) => t.line);
  const mapped = kind.mapping_required || value.mode === "mapped";
  const repeating = sources.objects.filter((o) => o.multiple);
  const fields = value.fields ?? [];
  const lines = value.lines ?? null;

  return (
    <div className="flex flex-col gap-4">
      {!kind.mapping_required && (
        <div role="radiogroup" aria-label="What to send" className="flex flex-col gap-2">
          {[
            { v: "record", title: "The record as it is", help: "Everything Syruss read, in the shape of your template." },
            { v: "mapped", title: "Only the fields I choose", help: "Rename fields, calculate values and fix the shape to match the other system." },
          ].map((o) => (
            <label key={o.v} className="flex cursor-pointer items-start gap-3 rounded-card border border-border bg-card p-3 has-[:checked]:border-control">
              <input type="radio" name="mapping-mode" className="mt-1 size-4 accent-[var(--ink)]" checked={(value.mode ?? "record") === o.v} onChange={() => onChange({ ...value, mode: o.v as "record" | "mapped" })} />
              <span><span className="block text-body font-medium text-ink">{o.title}</span><span className="block text-body text-muted">{o.help}</span></span>
            </label>
          ))}
        </div>
      )}
      {mapped && (
        <>
          <section className="flex flex-col gap-2">
            <h3 className="text-body font-medium text-ink">{kind.mapping_required ? `What ${kind.label} needs` : "Fields they receive"}</h3>
            <Rows rows={fields} sources={sources} fixed={kind.mapping_required ? fixedTargets : undefined} addLabel="Add a field" onChange={(rows) => onChange({ ...value, mode: "mapped", fields: rows })} />
          </section>
          {(kind.mapping_required ? lineTargets.length > 0 : true) && repeating.length > 0 && (
            <section className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-body font-medium text-ink">{kind.mapping_required ? "Line items" : "A list, one entry for each…"}</h3>
                {!kind.mapping_required && lines && <Button variant="ghost" size="sm" onClick={() => onChange({ ...value, lines: null })}>Remove the list</Button>}
              </div>
              {lines || kind.mapping_required ? (
                <>
                  <div className="max-w-xs">
                    <Select
                      label="Each entry is one" value={lines?.from ?? repeating[0].type}
                      onChange={(from) => onChange({ ...value, mode: "mapped", lines: { target: lines?.target ?? "lines", from, fields: [] } })}
                      options={repeating.map((o) => ({ value: o.type, label: o.type }))}
                    />
                  </div>
                  <Rows
                    rows={lines?.fields ?? []} sources={sources} only={lines?.from ?? repeating[0].type}
                    fixed={kind.mapping_required ? lineTargets : undefined} addLabel="Add a field to each entry"
                    onChange={(rows) => onChange({ ...value, mode: "mapped", lines: { target: lines?.target ?? "lines", from: lines?.from ?? repeating[0].type, fields: rows } })}
                  />
                </>
              ) : (
                <div><Button variant="secondary" size="sm" onClick={() => onChange({ ...value, mode: "mapped", lines: { target: "items", from: repeating[0].type, fields: [] } })}><Plus /> Add a list</Button></div>
              )}
            </section>
          )}
          <details className="rounded-card border border-border bg-card px-4 py-2.5">
            <summary className="cursor-pointer text-body text-muted">Calculations you can use</summary>
            <p className="mt-2 text-body text-muted">
              Write a calculation with the names of fields, such as <code className="font-mono text-meta">Invoice_total * 1.2</code>. Fields are named Object_field;
              inside a list, the entry’s own fields can be used by their plain name.
            </p>
            <ul className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-meta sm:grid-cols-2">{sources.functions.map((f) => <li key={f.name}><code className="font-mono text-ink">{f.usage}</code></li>)}</ul>
          </details>
        </>
      )}
    </div>
  );
}
