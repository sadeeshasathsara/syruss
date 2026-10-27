"use client";

import { Link2, Plus, Trash2 } from "lucide-react";
import { Button, IconButton } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { humanize } from "@/lib/format";
import { ValidationRulesEditor } from "./ValidationRulesEditor";
import type { TemplateField, TemplateObject, TemplateSchema } from "@/lib/types";

const TYPES = ["string", "number", "integer", "date", "time", "boolean"] as const;
const TYPE_LABEL: Record<string, string> = {
  string: "Text", number: "Number", integer: "Whole number", date: "Date", time: "Time", boolean: "Yes / no",
};

export function normalizeSchema(schema: TemplateSchema): TemplateSchema {
  return {
    ...schema,
    objects: (schema.objects ?? []).map((o) => ({
      ...o,
      fields: o.fields.map((f) => (typeof f === "string" ? { name: f, type: "string", required: false } : { type: "string", ...f })),
    })),
    validation_rules: schema.validation_rules ?? [],
  };
}

function ruleText(rule: TemplateSchema["validation_rules"] extends (infer R)[] | undefined ? R : never): string {
  if (rule.description) return rule.description;
  return `${humanize(String(rule.rule_type))} on ${rule.object}`;
}

/** Object types that have an identity block — these are the valid resolves_to targets. */
function identityTypes(schema: TemplateSchema): string[] {
  return schema.objects.filter((o) => o.identity).map((o) => o.type);
}

/**
 * Edit what the template captures: objects, their fields and field types. The agent's proposal is
 * the starting point; anything changed here is what gets published.
 */
export function SchemaEditor({ schema, onChange }: { schema: TemplateSchema; onChange: (s: TemplateSchema) => void }) {
  const entityTypes = identityTypes(schema);

  const setObject = (i: number, next: TemplateObject | null) => {
    const objects = [...schema.objects];
    if (next) objects[i] = next;
    else objects.splice(i, 1);
    const removedType = next ? null : schema.objects[i].type;
    onChange({
      ...schema,
      objects,
      validation_rules: removedType ? (schema.validation_rules ?? []).filter((r) => r.object !== removedType) : schema.validation_rules,
    });
  };

  return (
    <div className="flex flex-col gap-5">
      {/* The objects sit on a well, each a lifted card, so one object never runs into the next. */}
      <div className="flex flex-col gap-4 rounded-sheet bg-well p-2 sm:p-3">
        {schema.objects.map((obj, oi) => {
          const fields = obj.fields as TemplateField[];
          const isEntity = !!obj.identity;

          const setField = (fi: number, next: TemplateField | null) => {
            const list = [...fields];
            if (next) list[fi] = next;
            else list.splice(fi, 1);
            setObject(oi, { ...obj, fields: list });
          };

          const allSelected = fields.length > 0 && fields.every((f) => f.required);

          return (
            <section key={oi} className="rounded-card border border-border bg-card">
              <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-4 py-3">
                <div className="min-w-0 flex-1">
                  <h3 className="flex flex-wrap items-center gap-2 text-section font-semibold text-ink">
                    {obj.type}
                    {/* Entity badge */}
                    {isEntity && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-ink/8 px-2 py-0.5 text-meta font-normal text-muted">
                        <Link2 className="size-3" />
                        Entity
                      </span>
                    )}
                    <span className="num text-body font-normal text-muted">
                      {fields.length} field{fields.length === 1 ? "" : "s"}
                    </span>
                  </h3>
                  <p className="text-body text-muted">
                    {obj.multiple === false ? "One per document" : "Many per document"}
                  </p>
                </div>
                <div className="flex flex-none items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={fields.length === 0}
                    aria-label={`${allSelected ? "Deselect" : "Select"} all fields of ${obj.type} as required`}
                    onClick={() => setObject(oi, { ...obj, fields: fields.map((f) => ({ ...f, required: !allSelected })) })}
                  >
                    {allSelected ? "Deselect all" : "Select all"}
                  </Button>
                  <IconButton aria-label={`Remove ${obj.type}`} onClick={() => setObject(oi, null)}>
                    <Trash2 />
                  </IconButton>
                </div>
              </header>

              {obj.description && <p className="px-4 pt-3 text-body text-muted">{obj.description}</p>}

              {/* Identity configuration — shown when the object already has an identity block, or when the admin adds one */}
              <div className="border-b border-rule px-4 py-3">
                {isEntity ? (
                  <div className="flex flex-wrap items-end gap-3">
                    <p className="w-full text-meta font-medium text-ink">
                      Identity — how this entity is recognised across documents
                    </p>
                    <label className="flex flex-col gap-1">
                      <span className="text-meta text-muted">ID field</span>
                      <Select
                        label="ID field"
                        value={obj.identity!.id_field}
                        onChange={(id_field) => setObject(oi, { ...obj, identity: { ...obj.identity!, id_field } })}
                        options={fields.map((f) => ({ value: f.name, label: f.name }))}
                        className="h-8 w-44"
                      />
                    </label>
                    <label className="flex flex-col gap-1">
                      <span className="text-meta text-muted">Label field</span>
                      <Select
                        label="Label field"
                        value={obj.identity!.label_field}
                        onChange={(label_field) => setObject(oi, { ...obj, identity: { ...obj.identity!, label_field } })}
                        options={fields.map((f) => ({ value: f.name, label: f.name }))}
                        className="h-8 w-44"
                      />
                    </label>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setObject(oi, { ...obj, identity: undefined })}
                    >
                      Remove identity
                    </Button>
                  </div>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      const first = fields[0]?.name ?? "";
                      setObject(oi, { ...obj, identity: { id_field: first, label_field: first } });
                    }}
                  >
                    <Link2 /> Mark as entity (give it an identity)
                  </Button>
                )}
              </div>

              <div className="px-4 py-3">
                {/* Column headers — wider grid to accommodate the reference column */}
                <div className="hidden grid-cols-[minmax(0,1fr)_140px_minmax(0,160px)_90px_32px] gap-3 pb-1 text-meta text-muted @2xl:grid">
                  <span>Field</span>
                  <span>Type</span>
                  <span>References</span>
                  <span>Required</span>
                  <span />
                </div>
                <ul className="flex flex-col">
                  {fields.map((f, fi) => {
                    const resolvesTo = f.resolves_to ?? "";
                    return (
                      <li
                        key={fi}
                        className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-b border-rule py-2 last:border-0 @2xl:grid-cols-[minmax(0,1fr)_140px_minmax(0,160px)_90px_32px]"
                      >
                        <Input
                          aria-label="Field name"
                          value={f.name}
                          onChange={(e) => setField(fi, { ...f, name: e.target.value.replace(/\s+/g, "_").toLowerCase() })}
                          className="h-8 font-mono text-meta"
                        />
                        <IconButton aria-label={`Remove ${f.name}`} className="@2xl:order-last" onClick={() => setField(fi, null)}>
                          <Trash2 />
                        </IconButton>
                        <Select
                          label="Field type"
                          value={f.type ?? "string"}
                          onChange={(type) => setField(fi, { ...f, type, resolves_to: undefined })}
                          options={TYPES.map((t) => ({ value: t, label: TYPE_LABEL[t] }))}
                          className="h-8"
                        />
                        {/* References column — only string fields can point to an entity */}
                        <div className="flex items-center">
                          {f.type === "string" || !f.type ? (
                            entityTypes.length > 0 ? (
                              <Select
                                label="References"
                                value={resolvesTo || "__none__"}
                                onChange={(val) =>
                                  setField(fi, { ...f, resolves_to: val === "__none__" ? undefined : val })
                                }
                                options={[
                                  { value: "__none__", label: "— none —" },
                                  ...entityTypes.filter((t) => t !== obj.type).map((t) => ({ value: t, label: `→ ${t}` })),
                                ]}
                                className="h-8 w-full"
                              />
                            ) : (
                              <span className="text-meta text-muted">No entities yet</span>
                            )
                          ) : resolvesTo ? (
                            /* Non-string field had a resolves_to — show it read-only and let them clear it */
                            <span className="inline-flex items-center gap-1 rounded-full bg-ink/8 px-2 py-0.5 text-meta text-muted">
                              <Link2 className="size-3" />→ {resolvesTo}
                            </span>
                          ) : (
                            <span className="text-meta text-muted">—</span>
                          )}
                        </div>
                        <label className="flex items-center gap-2 text-body text-ink">
                          <input
                            type="checkbox"
                            checked={!!f.required}
                            onChange={(e) => setField(fi, { ...f, required: e.target.checked })}
                            className="size-4 accent-[var(--ink)]"
                          />
                          <span className="@2xl:sr-only">Required</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
                <Button
                  size="sm"
                  variant="ghost"
                  className="mt-2"
                  onClick={() => setObject(oi, { ...obj, fields: [...fields, { name: `field_${fields.length + 1}`, type: "string", required: false }] })}
                >
                  <Plus /> Add field
                </Button>
              </div>
            </section>
          );
        })}
      </div>

      <section className="mt-4 border-t border-border pt-4">
        <ValidationRulesEditor
          schema={schema}
          onChange={(validation_rules) => onChange({ ...schema, validation_rules })}
        />
      </section>
    </div>
  );
}
