"use client";

import { Pencil } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { Tooltip } from "@/components/ui/Tooltip";
import { cn, display, fieldName, humanize, percent, rowsOf } from "@/lib/format";
import type { Extraction, FieldEvidence, TemplateSchema } from "@/lib/types";

type Data = Extraction["extracted_data"];

function evidenceIndex(evidence: FieldEvidence[]) {
  const map = new Map<string, FieldEvidence>();
  for (const e of evidence) if (e.field) map.set(`${e.object ?? ""}|${e.index ?? 0}|${e.field}`, e);
  return (object: string, index: number, field: string) =>
    map.get(`${object}|${index}|${field}`) ?? map.get(`|${index}|${field}`) ?? map.get(`${object}|0|${field}`);
}

/** Same characters, ignoring punctuation, spacing, case and order: "19.00" vs "19:00", "21.07.2026" vs "2026-07-21". */
function sameWriting(a: string, b: string): boolean {
  const key = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "").split("").sort().join("");
  return key(a) === key(b);
}

/** For fields that refer to another thing in the record: identifying value -> that thing's name. */
function referenceIndex(data: Data, schema?: TemplateSchema) {
  const key = (v: unknown) => String(v ?? "").replace(/\s+/g, "").toUpperCase();
  const names = new Map<string, Map<string, string>>();
  for (const o of schema?.objects ?? []) {
    if (!o.identity) continue;
    const m = new Map<string, string>();
    for (const r of rowsOf(data[o.type])) {
      const label = r[o.identity.label_field];
      if (r[o.identity.id_field] != null && label != null && label !== "") m.set(key(r[o.identity.id_field]), String(label));
    }
    names.set(o.type, m);
  }
  return (target: string | undefined, value: unknown) => (target ? names.get(target)?.get(key(value)) : undefined);
}

/** A value, with what was literally written when the agent interpreted it. */
function Value({ value, ev, uncertain, refers, editable, onEdit }: {
  value: unknown;
  ev?: FieldEvidence;
  uncertain?: string;
  refers?: string;
  editable?: boolean;
  onEdit?: (v: string) => void;
}) {
  const raw = ev?.raw_text && !sameWriting(String(ev.raw_text), String(value ?? "")) ? String(ev.raw_text) : null;
  const body = editable ? (
    <input
      value={value === null || value === undefined ? "" : String(value)}
      onChange={(e) => onEdit?.(e.target.value)}
      aria-label="Value"
      className={cn(
        "h-8 w-full min-w-24 rounded-control border bg-sheet px-2 text-body pointer-coarse:h-10",
        uncertain ? "border-[var(--pill-warning-ink)]" : "border-control",
      )}
    />
  ) : (
    <span className={cn("break-words", value === null || value === undefined || value === "" ? "text-faint" : "text-ink")}>{display(value)}</span>
  );
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      {body}
      {refers && <span className="text-meta text-muted">{refers}</span>}
      {raw && <span className="text-meta text-muted">Written as “{raw}”</span>}
      {uncertain && <span className="text-meta text-[var(--pill-warning-ink)]">{uncertain}</span>}
      {ev?.confidence !== undefined && ev.confidence < 0.9 && !uncertain && (
        <Tooltip label="How sure the agent was about this field">
          <span className="w-fit text-meta text-muted">{percent(ev.confidence)} sure</span>
        </Tooltip>
      )}
    </span>
  );
}

function setValue(source: Data, object: string, index: number, field: string, value: string): Data {
  const rows = rowsOf(source[object]).map((r) => ({ ...r }));
  rows[index] = { ...rows[index], [field]: value };
  return { ...source, [object]: Array.isArray(source[object]) ? rows : rows[0] };
}

/**
 * The record, laid out by the template: single objects as label/value rows, repeated objects as
 * a table (two-line items on narrow screens). Editable as a whole (corrections in review), or box
 * by box with `onCorrect`: each box gets an Edit button, and while editing, a note telling Syruss
 * what was wrong.
 */
export function RecordView({
  data,
  schema,
  evidence = [],
  uncertain = [],
  editable,
  onChange,
  onCorrect,
}: {
  data: Data;
  schema?: TemplateSchema;
  evidence?: FieldEvidence[];
  uncertain?: { field: string; why?: string }[];
  editable?: boolean;
  onChange?: (next: Data) => void;
  onCorrect?: (object: string, next: Data, reason: string) => Promise<void>;
}) {
  const [correcting, setCorrecting] = useState<{ object: string; draft: Data; reason: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const findEv = evidenceIndex(evidence);
  const refName = referenceIndex(data, schema);
  const objectOrder = [
    ...(schema?.objects ?? []).map((o) => o.type).filter((t) => t in data),
    ...Object.keys(data).filter((k) => !(schema?.objects ?? []).some((o) => o.type === k)),
  ];
  const uncertainFor = (object: string, index: number, field: string) =>
    uncertain.find((u) => u.field === `${object}[${index}].${field}` || u.field === `${object}.${field}` || u.field === field)?.why;

  const edit = (object: string, index: number, field: string, value: string) => {
    if (correcting?.object === object) {
      setCorrecting({ ...correcting, draft: setValue(correcting.draft, object, index, field, value) });
      return;
    }
    if (onChange) onChange(setValue(data, object, index, field, value));
  };
  const save = async () => {
    if (!correcting || !onCorrect) return;
    setSaving(true);
    setError(null);
    try {
      await onCorrect(correcting.object, correcting.draft, correcting.reason.trim());
      setCorrecting(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save the correction.");
    } finally {
      setSaving(false);
    }
  };
  const changed = !!correcting && JSON.stringify(correcting.draft[correcting.object]) !== JSON.stringify(data[correcting.object]);

  // Lays itself out by its own width (a container), not the window's.
  return (
    <div className="@container flex flex-col gap-6">
      {objectOrder.map((object) => {
        const spec = schema?.objects.find((o) => o.type === object);
        const isCorrecting = correcting?.object === object;
        const rows = rowsOf((isCorrecting ? correcting.draft : data)[object]);
        const editable_ = editable || isCorrecting;
        const fields = [
          ...(spec?.fields.map(fieldName) ?? []),
          ...[...new Set(rows.flatMap((r) => Object.keys(r)))].filter((f) => !spec?.fields.map(fieldName).includes(f)),
        ];
        const single = spec ? spec.multiple === false : !Array.isArray(data[object]);
        const refers = (row: Record<string, unknown>, f: string) => {
          const fieldSpec = spec?.fields.find((x) => typeof x !== "string" && x.name === f);
          return typeof fieldSpec === "object" ? refName(fieldSpec.resolves_to, row[f]) : undefined;
        };

        return (
          <section
            key={object}
            className={cn(onCorrect && "rounded-card border p-3 transition-colors", onCorrect && (isCorrecting ? "border-control bg-card" : "border-border"))}
          >
            <div className="flex items-center gap-2">
              <h3 className="min-w-0 flex-1 text-section font-semibold text-ink">
                {object}
                {!single && <span className="num ml-2 text-body font-normal text-muted">{rows.length}</span>}
              </h3>
              {onCorrect && !isCorrecting && rows.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!!correcting}
                  onClick={() => {
                    setError(null);
                    setCorrecting({ object, draft: data, reason: "" });
                  }}
                >
                  <Pencil /> Edit
                </Button>
              )}
            </div>
            {rows.length === 0 ? (
              <p className="mt-2 text-body text-muted">None on this document.</p>
            ) : single ? (
              <dl className="mt-2 grid grid-cols-1 gap-x-6 @md:grid-cols-[minmax(120px,max-content)_minmax(0,1fr)]">
                {fields.map((f) => (
                  <div key={f} className="contents">
                    <dt className="pt-2 text-body text-muted @md:border-t @md:border-rule @md:py-2">{humanize(f)}</dt>
                    <dd className="border-b border-rule pb-2 text-body @md:border-b-0 @md:border-t @md:py-2">
                      <Value value={rows[0][f]} ev={findEv(object, 0, f)} uncertain={uncertainFor(object, 0, f)} refers={refers(rows[0], f)} editable={editable_} onEdit={(v) => edit(object, 0, f, v)} />
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <>
                {/* Wide: a table. */}
                <div className="mt-2 hidden overflow-x-auto scrollbar-thin @4xl:block">
                  <table className="w-full border-collapse text-body">
                    <thead>
                      <tr className="text-left text-meta text-muted">
                        {fields.map((f) => <th key={f} className="py-2 pr-4 font-normal">{humanize(f)}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={i} className="border-t border-rule align-top">
                          {fields.map((f) => (
                            <td key={f} className="py-2 pr-4">
                              <Value value={r[f]} ev={findEv(object, i, f)} uncertain={uncertainFor(object, i, f)} refers={refers(r, f)} editable={editable_} onEdit={(v) => edit(object, i, f, v)} />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {/* Narrow: one block per row, label beside value. */}
                <ol className="mt-2 grid grid-cols-1 gap-3 @2xl:grid-cols-2 @4xl:hidden">
                  {rows.map((r, i) => (
                    <li key={i} className="rounded-card border border-border px-3 py-2">
                      <dl className="grid grid-cols-[minmax(96px,40%)_minmax(0,1fr)] gap-x-3 gap-y-1.5">
                        {fields.map((f) => (
                          <div key={f} className="contents">
                            <dt className="text-body text-muted">{humanize(f)}</dt>
                            <dd className="text-body">
                              <Value value={r[f]} ev={findEv(object, i, f)} uncertain={uncertainFor(object, i, f)} refers={refers(r, f)} editable={editable_} onEdit={(v) => edit(object, i, f, v)} />
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </li>
                  ))}
                </ol>
              </>
            )}
            {isCorrecting && (
              <div className="mt-3 flex animate-rise flex-col gap-2 border-t border-rule pt-3">
                <label htmlFor={`why-${object}`} className="text-body font-medium text-ink">What was wrong?</label>
                <Textarea
                  id={`why-${object}`}
                  value={correcting.reason}
                  onChange={(e) => setCorrecting({ ...correcting, reason: e.target.value })}
                  placeholder="Tell Syruss what it got wrong, so it reads the next ones right. For example: the surname was misread, the D in the shift column means day."
                  maxLength={2000}
                  className="min-h-20"
                />
                <p className="text-meta text-muted">Saving updates the record and sends your changes and this note to Syruss in the chat.</p>
                {error && <p className="text-body text-negative">{error}</p>}
                <div className="flex gap-2">
                  <Button variant="primary" size="sm" disabled={!changed || saving} onClick={save}>
                    {saving ? "Saving…" : "Save and tell Syruss"}
                  </Button>
                  <Button variant="ghost" size="sm" disabled={saving} onClick={() => setCorrecting(null)}>Cancel</Button>
                </div>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
