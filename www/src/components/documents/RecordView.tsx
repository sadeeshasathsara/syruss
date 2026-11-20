"use client";

import { Pencil } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { Tooltip } from "@/components/ui/Tooltip";
import { cn, display, fieldName, humanize, percent, rowsOf } from "@/lib/format";
import type { Extraction, FieldEvidence, TemplateSchema } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

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
function Value({ value, ev, fieldConfidence, uncertain, refers, editable, onEdit, to }: {
  value: unknown;
  /** Where the thing this value names has its page (an identity, or a reference to one) */
  to?: string;
  ev?: FieldEvidence;
  fieldConfidence?: number;
  uncertain?: string;
  refers?: string;
  editable?: boolean;
  onEdit?: (v: string) => void;
}) {
  const conf = fieldConfidence ?? ev?.confidence;
  const raw = ev?.raw_text && !sameWriting(String(ev.raw_text), String(value ?? "")) ? String(ev.raw_text) : null;
  
  // Field-level color coding per Phase 4 spec:
  // >= 90%: green (verified by math, format, or master data)
  // 70-89%: amber (visual reading only)
  // < 70%: red (discrepancy or unverified)
  const confTone = conf !== undefined ? (conf >= 0.9 ? "positive" : conf >= 0.7 ? "warning" : "negative") : null;

  const body = editable ? (
    <input
      value={value === null || value === undefined ? "" : String(value)}
      onChange={(e) => onEdit?.(e.target.value)}
      aria-label="Value"
      className={cn(
        "h-8 w-full min-w-24 rounded-control border bg-sheet px-2 text-body pointer-coarse:h-10",
        uncertain
          ? "border-[var(--pill-warning-ink)]"
          : confTone === "negative"
          ? "border-[var(--pill-negative-ink)]/50 focus:border-[var(--pill-negative-ink)]"
          : "border-control",
      )}
    />
  ) : (
    to && value !== null && value !== undefined && value !== "" ? (
      <Link href={to} className="break-words font-medium text-ink underline decoration-border decoration-1 underline-offset-4 hover:decoration-ink">{display(value)}</Link>
    ) : (
      <span className={cn("break-words", value === null || value === undefined || value === "" ? "text-faint" : "text-ink")}>{display(value)}</span>
    )
  );

  const confBadge = conf !== undefined ? (
    conf >= 0.9 ? (
      <Tooltip label={`Verified by rules, format, or master data (${percent(conf)})`}>
        <span className="inline-flex items-center gap-1 text-[11px] text-[var(--pill-positive-ink)]">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--pill-positive-ink)]" />
          {percent(conf)}
        </span>
      </Tooltip>
    ) : conf >= 0.7 ? (
      <Tooltip label={`Visual model reading only (${percent(conf)})`}>
        <span className="inline-flex items-center gap-1 text-[11px] text-[var(--pill-warning-ink)]">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--pill-warning-ink)]" />
          {percent(conf)}
        </span>
      </Tooltip>
    ) : (
      <Tooltip label={`Low confidence (${percent(conf)}) — verify against document`}>
        <span className="inline-flex items-center gap-1 text-[11px] text-[var(--pill-negative-ink)] font-semibold">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--pill-negative-ink)]" />
          {percent(conf)}
        </span>
      </Tooltip>
    )
  ) : null;

  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="flex items-center gap-2">
        {body}
        {confBadge}
      </span>
      {refers && <span className="text-meta text-muted">{refers}</span>}
      {raw && <span className="text-meta text-muted">Written as “{raw}”</span>}
      {uncertain && <span className="text-meta text-[var(--pill-warning-ink)]">{uncertain}</span>}
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
  cards,
  fieldConfidences,
}: {
  data: Data;
  schema?: TemplateSchema;
  evidence?: FieldEvidence[];
  uncertain?: { field: string; why?: string }[];
  editable?: boolean;
  onChange?: (next: Data) => void;
  onCorrect?: (object: string, next: Data, reason: string) => Promise<void>;
  /** Each object in a card of its own (on a page, where it would otherwise blend into the background) */
  cards?: boolean;
  fieldConfidences?: Record<string, number>;
}) {
  const { href } = useWorkspace();
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
        // A thing's id and name, and a field that points to another thing, open that thing's page
        const thingPath = (type: string, v: unknown) => href(`objects/${encodeURIComponent(type)}/things/${encodeURIComponent(String(v).replace(/\s+/g, "").toUpperCase())}`);
        const link = (row: Record<string, unknown>, f: string): string | undefined => {
          if (editable_) return undefined;
          if (spec?.identity && (f === spec.identity.id_field || f === spec.identity.label_field) && row[spec.identity.id_field] != null && row[spec.identity.id_field] !== "") {
            return thingPath(spec.type, row[spec.identity.id_field]);
          }
          const fieldSpec = spec?.fields.find((x) => typeof x !== "string" && x.name === f);
          const target = typeof fieldSpec === "object" ? fieldSpec.resolves_to : undefined;
          const identified = target && schema?.objects.find((o) => o.type === target)?.identity;
          return identified && row[f] != null && row[f] !== "" ? thingPath(target, row[f]) : undefined;
        };
        const refers = (row: Record<string, unknown>, f: string) => {
          const fieldSpec = spec?.fields.find((x) => typeof x !== "string" && x.name === f);
          return typeof fieldSpec === "object" ? refName(fieldSpec.resolves_to, row[f]) : undefined;
        };

        return (
          <section
            key={object}
            className={cards
              ? cn("rounded-card bg-card p-4 [box-shadow:var(--shadow-card)] transition-shadow", isCorrecting && "ring-1 ring-control")
              : cn(onCorrect && "rounded-card border p-3 transition-colors", onCorrect && (isCorrecting ? "border-control bg-card" : "border-border"))}
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
                      <Value value={rows[0][f]} ev={findEv(object, 0, f)} fieldConfidence={fieldConfidences?.[`${object}.${f}`] ?? fieldConfidences?.[`${object}[0].${f}`]} uncertain={uncertainFor(object, 0, f)} refers={refers(rows[0], f)} to={link(rows[0], f)} editable={editable_} onEdit={(v) => edit(object, 0, f, v)} />
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
                              <Value value={r[f]} ev={findEv(object, i, f)} fieldConfidence={fieldConfidences?.[`${object}[${i}].${f}`] ?? fieldConfidences?.[`${object}.${f}`]} uncertain={uncertainFor(object, i, f)} refers={refers(r, f)} to={link(r, f)} editable={editable_} onEdit={(v) => edit(object, i, f, v)} />
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
                    <li key={i} className={cards ? "rounded-control bg-raised px-3 py-2" : "rounded-card border border-border px-3 py-2"}>
                      <dl className="grid grid-cols-[minmax(96px,40%)_minmax(0,1fr)] gap-x-3 gap-y-1.5">
                        {fields.map((f) => (
                          <div key={f} className="contents">
                            <dt className="text-body text-muted">{humanize(f)}</dt>
                            <dd className="text-body">
                              <Value value={r[f]} ev={findEv(object, i, f)} fieldConfidence={fieldConfidences?.[`${object}[${i}].${f}`] ?? fieldConfidences?.[`${object}.${f}`]} uncertain={uncertainFor(object, i, f)} refers={refers(r, f)} to={link(r, f)} editable={editable_} onEdit={(v) => edit(object, i, f, v)} />
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
                  placeholder="Tell Syruss what it got wrong, so it reads the next ones right. For example: the name was misread, or a code on the page means something specific."
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
