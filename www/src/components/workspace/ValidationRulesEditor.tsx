"use client";

import { useMemo, useState } from "react";
import { Check, Code, Edit3, HelpCircle, Plus, Sliders, Trash2, X } from "lucide-react";
import { Button, IconButton } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { cn, fieldName, humanize } from "@/lib/format";
import type { TemplateField, TemplateSchema, ValidationRule } from "@/lib/types";

export const RULE_TYPES = [
  { value: "expression", label: "Formula / Math Expression", tone: "accent" as const, desc: "Safe math/logic formula evaluated on each record" },
  { value: "sum_match", label: "Sum of Rows Matches Total", tone: "positive" as const, desc: "Sum of line items must equal a header total" },
  { value: "count_equals", label: "Row Count Check", tone: "neutral" as const, desc: "Number of rows must equal an expected count" },
  { value: "allowed_values", label: "Allowed Values (Whitelist)", tone: "accent" as const, desc: "Restricts a field to specific valid values" },
  { value: "regex", label: "Pattern Match (Regex)", tone: "neutral" as const, desc: "Field format must match a regular expression" },
  { value: "unique", label: "Unique Field", tone: "positive" as const, desc: "Ensures no duplicate values appear across records" },
  { value: "time_diff", label: "Time Duration Check", tone: "neutral" as const, desc: "Verifies duration between start and end time" },
] as const;

export function formatRuleCode(rule: ValidationRule): string {
  switch (rule.rule_type) {
    case "expression":
      return String(rule.expression ?? "true");
    case "sum_match":
      return `sum(${rule.object}.${rule.field}) == ${rule.total_object}.${rule.total_field}${
        rule.tolerance !== undefined ? ` (±${rule.tolerance})` : ""
      }`;
    case "count_equals":
      if (rule.expected_count !== undefined) {
        return `count(${rule.object}) == ${rule.expected_count}`;
      }
      return `count(${rule.object}) == ${rule.count_object}.${rule.count_field}`;
    case "allowed_values": {
      const vals = Array.isArray(rule.values) ? rule.values.join(", ") : "";
      return `${rule.object}.${rule.field} in [${vals}]`;
    }
    case "regex":
      return `${rule.object}.${rule.field} matches /${rule.pattern ?? ""}/`;
    case "unique":
      return `unique(${rule.object}.${rule.field})`;
    case "time_diff":
      return `diff(${rule.start_field}, ${rule.end_field}) == ${rule.hours_field ?? rule.expected_hours ?? "?"}h (±${
        rule.tolerance_hours ?? 0.25
      }h)`;
    default:
      return `${rule.rule_type} on ${rule.object}`;
  }
}

export function formatRuleDescription(rule: ValidationRule): string {
  if (rule.description?.trim()) return rule.description;
  switch (rule.rule_type) {
    case "expression":
      return `${rule.object} must satisfy: ${rule.expression}`;
    case "sum_match":
      return `Sum of ${rule.object} ${rule.field} matches ${rule.total_object} ${rule.total_field}`;
    case "count_equals":
      return rule.expected_count !== undefined
        ? `Found exactly ${rule.expected_count} ${rule.object} records`
        : `${rule.object} count matches ${rule.count_object} ${rule.count_field}`;
    case "allowed_values":
      return `${rule.object} ${rule.field} must be one of: ${(rule.values ?? []).join(", ")}`;
    case "regex":
      return `${rule.object} ${rule.field} must match format ${rule.pattern}`;
    case "unique":
      return `${rule.object} ${rule.field} must be unique within document`;
    case "time_diff":
      return `Time difference between ${rule.start_field} and ${rule.end_field} matches reported hours`;
    default:
      return `${humanize(rule.rule_type)} on ${rule.object}`;
  }
}

interface ValidationRulesEditorProps {
  schema: TemplateSchema;
  onChange: (rules: ValidationRule[]) => void;
}

export function ValidationRulesEditor({ schema, onChange }: ValidationRulesEditorProps) {
  const rules = schema.validation_rules ?? [];
  const [editingIndex, setEditingIndex] = useState<number | "new" | null>(null);

  // Form state
  const [targetObject, setTargetObject] = useState<string>("");
  const [ruleType, setRuleType] = useState<string>("expression");
  const [description, setDescription] = useState<string>("");

  // Type-specific form fields
  const [expression, setExpression] = useState<string>("");
  const [field, setField] = useState<string>("");
  const [totalObject, setTotalObject] = useState<string>("");
  const [totalField, setTotalField] = useState<string>("");
  const [tolerance, setTolerance] = useState<string>("0.01");

  const [countMode, setCountMode] = useState<"fixed" | "field">("fixed");
  const [expectedCount, setExpectedCount] = useState<string>("1");
  const [countObject, setCountObject] = useState<string>("");
  const [countField, setCountField] = useState<string>("");

  const [allowedValuesText, setAllowedValuesText] = useState<string>("");
  const [regexPattern, setRegexPattern] = useState<string>("");

  const [startField, setStartField] = useState<string>("");
  const [endField, setEndField] = useState<string>("");
  const [hoursMode, setHoursMode] = useState<"field" | "fixed">("field");
  const [hoursField, setHoursField] = useState<string>("");
  const [expectedHours, setExpectedHours] = useState<string>("8.0");
  const [toleranceHours, setToleranceHours] = useState<string>("0.25");

  const objectNames = useMemo(() => schema.objects.map((o) => o.type), [schema.objects]);

  // Object fields helper
  const getFieldsFor = (objType: string): string[] => {
    const obj = schema.objects.find((o) => o.type === objType);
    if (!obj) return [];
    return obj.fields.map((f) => fieldName(f));
  };

  const currentObjFields = useMemo(() => getFieldsFor(targetObject), [schema.objects, targetObject]);
  const totalObjFields = useMemo(() => getFieldsFor(totalObject), [schema.objects, totalObject]);
  const countObjFields = useMemo(() => getFieldsFor(countObject), [schema.objects, countObject]);

  const startNewRule = () => {
    const defaultObj = objectNames[0] ?? "";
    const defaultFields = getFieldsFor(defaultObj);
    setTargetObject(defaultObj);
    setRuleType("expression");
    setDescription("");
    setExpression("");
    setField(defaultFields[0] ?? "");
    setTotalObject(objectNames.find((o) => o !== defaultObj) ?? defaultObj);
    setTotalField("");
    setTolerance("0.01");
    setCountMode("fixed");
    setExpectedCount("1");
    setCountObject(defaultObj);
    setCountField("");
    setAllowedValuesText("");
    setRegexPattern("");
    setStartField(defaultFields[0] ?? "");
    setEndField(defaultFields[1] ?? defaultFields[0] ?? "");
    setHoursMode("field");
    setHoursField(defaultFields[2] ?? defaultFields[0] ?? "");
    setExpectedHours("8.0");
    setToleranceHours("0.25");
    setEditingIndex("new");
  };

  const startEditRule = (idx: number) => {
    const r = rules[idx];
    if (!r) return;
    setTargetObject(r.object ?? objectNames[0] ?? "");
    setRuleType(r.rule_type ?? "expression");
    setDescription(r.description ?? "");
    setExpression(r.expression ?? "");
    setField(r.field ?? "");
    setTotalObject(r.total_object ?? objectNames[0] ?? "");
    setTotalField(r.total_field ?? "");
    setTolerance(r.tolerance !== undefined ? String(r.tolerance) : "0.01");

    if (r.expected_count !== undefined) {
      setCountMode("fixed");
      setExpectedCount(String(r.expected_count));
      setCountObject(objectNames[0] ?? "");
      setCountField("");
    } else {
      setCountMode("field");
      setCountObject(r.count_object ?? objectNames[0] ?? "");
      setCountField(r.count_field ?? "");
      setExpectedCount("1");
    }

    setAllowedValuesText(Array.isArray(r.values) ? r.values.join(", ") : "");
    setRegexPattern(r.pattern ?? "");
    setStartField(r.start_field ?? "");
    setEndField(r.end_field ?? "");

    if (r.expected_hours !== undefined) {
      setHoursMode("fixed");
      setExpectedHours(String(r.expected_hours));
      setHoursField("");
    } else {
      setHoursMode("field");
      setHoursField(r.hours_field ?? "");
      setExpectedHours("8.0");
    }
    setToleranceHours(r.tolerance_hours !== undefined ? String(r.tolerance_hours) : "0.25");
    setEditingIndex(idx);
  };

  const saveRule = () => {
    if (!targetObject) return;

    const base: ValidationRule = {
      id:
        editingIndex !== "new" && editingIndex !== null && rules[editingIndex]?.id
          ? rules[editingIndex].id
          : `rule_${Date.now().toString(36)}`,
      rule_type: ruleType,
      object: targetObject,
      description: description.trim() || undefined,
    };

    switch (ruleType) {
      case "expression":
        base.expression = expression.trim();
        break;
      case "sum_match":
        base.field = field;
        base.total_object = totalObject;
        base.total_field = totalField;
        base.tolerance = tolerance ? parseFloat(tolerance) : 0.01;
        break;
      case "count_equals":
        if (countMode === "fixed") {
          base.expected_count = parseInt(expectedCount, 10) || 1;
        } else {
          base.count_object = countObject;
          base.count_field = countField;
        }
        break;
      case "allowed_values":
        base.field = field;
        base.values = allowedValuesText
          .split(",")
          .map((v) => v.trim())
          .filter(Boolean);
        break;
      case "regex":
        base.field = field;
        base.pattern = regexPattern.trim();
        break;
      case "unique":
        base.field = field;
        break;
      case "time_diff":
        base.start_field = startField;
        base.end_field = endField;
        if (hoursMode === "fixed") {
          base.expected_hours = parseFloat(expectedHours) || 8.0;
        } else {
          base.hours_field = hoursField;
        }
        base.tolerance_hours = toleranceHours ? parseFloat(toleranceHours) : 0.25;
        break;
    }

    const nextRules = [...rules];
    if (editingIndex === "new") {
      nextRules.push(base);
    } else if (editingIndex !== null) {
      nextRules[editingIndex] = base;
    }
    onChange(nextRules);
    setEditingIndex(null);
  };

  const removeRule = (idx: number) => {
    const nextRules = rules.filter((_, i) => i !== idx);
    onChange(nextRules);
    if (editingIndex === idx) setEditingIndex(null);
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-section font-semibold text-ink">Validation Rules & Checks</h3>
          <p className="text-body text-muted">
            Deterministic rules checked by the server before any record is committed.
          </p>
        </div>
        {editingIndex === null && (
          <Button
            size="sm"
            variant="secondary"
            onClick={startNewRule}
            disabled={objectNames.length === 0}
            className="flex-none"
          >
            <Plus className="size-4" /> Add validation rule
          </Button>
        )}
      </div>

      {/* Rules list */}
      {rules.length === 0 && editingIndex === null ? (
        <div className="rounded-card border border-dashed border-border p-6 text-center">
          <p className="text-body text-muted">No validation rules defined yet.</p>
          <p className="mt-1 text-meta text-muted">
            Add rules to enforce mathematical totals, line item balances, allowed values, or regex patterns.
          </p>
          <Button size="sm" variant="ghost" onClick={startNewRule} disabled={objectNames.length === 0} className="mt-3">
            <Plus className="size-4" /> Add your first rule
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {rules.map((rule, idx) => {
            const ruleMeta = RULE_TYPES.find((t) => t.value === rule.rule_type) ?? {
              label: humanize(rule.rule_type),
              tone: "neutral" as const,
            };
            const isEditingThis = editingIndex === idx;

            if (isEditingThis) return null; // rendered in the builder form below

            return (
              <div
                key={rule.id ?? idx}
                className="flex flex-col gap-2 rounded-card border border-border bg-card p-3 transition-colors hover:border-control sm:p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={ruleMeta.tone}>{ruleMeta.label}</Pill>
                    <span className="inline-flex items-center rounded-control bg-well px-2 py-0.5 font-mono text-meta font-medium text-ink">
                      {rule.object}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    <IconButton aria-label="Edit rule" onClick={() => startEditRule(idx)} className="h-7 w-7">
                      <Edit3 className="size-3.5" />
                    </IconButton>
                    <IconButton aria-label="Remove rule" onClick={() => removeRule(idx)} className="h-7 w-7 text-negative">
                      <Trash2 className="size-3.5" />
                    </IconButton>
                  </div>
                </div>

                <p className="text-body font-medium text-ink">{formatRuleDescription(rule)}</p>

                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-meta text-muted">Expression:</span>
                  <code className="rounded bg-well px-2 py-0.5 font-mono text-meta text-ink">
                    {formatRuleCode(rule)}
                  </code>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Inline Rule Builder / Editor Form */}
      {editingIndex !== null && (
        <section className="rounded-card border-2 border-accent/40 bg-card p-4 shadow-sm sm:p-5">
          <header className="mb-4 flex items-center justify-between border-b border-border pb-3">
            <div>
              <h4 className="text-section font-semibold text-ink">
                {editingIndex === "new" ? "Add Validation Rule" : "Edit Validation Rule"}
              </h4>
              <p className="text-meta text-muted">Configure the constraint logic and error description.</p>
            </div>
            <IconButton aria-label="Cancel" onClick={() => setEditingIndex(null)}>
              <X className="size-4" />
            </IconButton>
          </header>

          <div className="flex flex-col gap-4">
            {/* Top selectors: Target Object & Rule Type */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Target Object">
                {() => (
                  <Select
                    value={targetObject}
                    onChange={(val) => {
                      setTargetObject(val);
                      const flds = getFieldsFor(val);
                      setField(flds[0] ?? "");
                    }}
                    options={objectNames.map((o) => ({ value: o, label: o }))}
                    label="Target Object"
                  />
                )}
              </Field>

              <Field label="Rule Type">
                {() => (
                  <Select
                    value={ruleType}
                    onChange={(val) => setRuleType(val)}
                    options={RULE_TYPES.map((t) => ({ value: t.value, label: t.label }))}
                    label="Rule Type"
                  />
                )}
              </Field>
            </div>

            {/* Rule Specific Form Controls */}
            {ruleType === "expression" && (
              <div className="flex flex-col gap-2 rounded-sheet bg-well p-3">
                <Field
                  label="Python/AST Expression"
                  help="Safe expression evaluated for each row. Standard math operators and abs(), min(), max(), round() are supported."
                >
                  {() => (
                    <Input
                      value={expression}
                      onChange={(e) => setExpression(e.target.value)}
                      placeholder="e.g. abs(amount - quantity * unit_price) < 0.01"
                      className="font-mono text-meta"
                    />
                  )}
                </Field>

                {currentObjFields.length > 0 && (
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <span className="text-meta text-muted">Insert field:</span>
                    {currentObjFields.map((fName) => (
                      <button
                        type="button"
                        key={fName}
                        onClick={() => setExpression((prev) => (prev ? `${prev} ${fName}` : fName))}
                        className="rounded bg-sheet px-2 py-0.5 font-mono text-meta text-ink border border-border transition-colors hover:border-accent hover:text-accent"
                      >
                        +{fName}
                      </button>
                    ))}
                  </div>
                )}

                <div className="mt-2 flex flex-wrap gap-2 text-meta text-muted">
                  <span className="font-medium">Quick examples:</span>
                  <button
                    type="button"
                    className="underline hover:text-ink"
                    onClick={() => setExpression("abs(amount - quantity * unit_price) < 0.01")}
                  >
                    amount == qty * price
                  </button>
                  <span>•</span>
                  <button
                    type="button"
                    className="underline hover:text-ink"
                    onClick={() => setExpression("hours <= 16")}
                  >
                    hours &lt;= 16
                  </button>
                  <span>•</span>
                  <button
                    type="button"
                    className="underline hover:text-ink"
                    onClick={() => setExpression("amount >= 0")}
                  >
                    positive amount
                  </button>
                </div>
              </div>
            )}

            {ruleType === "sum_match" && (
              <div className="grid grid-cols-1 gap-4 rounded-sheet bg-well p-3 sm:grid-cols-2">
                <Field label={`Sum of field in ${targetObject}`}>
                  {() => (
                    <Select
                      value={field}
                      onChange={(val) => setField(val)}
                      options={currentObjFields.map((f) => ({ value: f, label: f }))}
                      label="Field to Sum"
                    />
                  )}
                </Field>

                <Field label="Total Object (Header)">
                  {() => (
                    <Select
                      value={totalObject}
                      onChange={(val) => {
                        setTotalObject(val);
                        const flds = getFieldsFor(val);
                        setTotalField(flds[0] ?? "");
                      }}
                      options={objectNames.map((o) => ({ value: o, label: o }))}
                      label="Total Object"
                    />
                  )}
                </Field>

                <Field label={`Total field in ${totalObject}`}>
                  {() => (
                    <Select
                      value={totalField}
                      onChange={(val) => setTotalField(val)}
                      options={totalObjFields.map((f) => ({ value: f, label: f }))}
                      label="Total Field"
                    />
                  )}
                </Field>

                <Field label="Tolerance (default 0.01)">
                  {() => (
                    <Input
                      type="number"
                      step="0.001"
                      value={tolerance}
                      onChange={(e) => setTolerance(e.target.value)}
                      placeholder="0.01"
                    />
                  )}
                </Field>
              </div>
            )}

            {ruleType === "count_equals" && (
              <div className="flex flex-col gap-3 rounded-sheet bg-well p-3">
                <div className="flex items-center gap-4 text-body">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      checked={countMode === "fixed"}
                      onChange={() => setCountMode("fixed")}
                      className="accent-[var(--ink)]"
                    />
                    <span>Fixed count</span>
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      checked={countMode === "field"}
                      onChange={() => setCountMode("field")}
                      className="accent-[var(--ink)]"
                    />
                    <span>Match count field in header object</span>
                  </label>
                </div>

                {countMode === "fixed" ? (
                  <Field label="Expected row count">
                    {() => (
                      <Input
                        type="number"
                        min="0"
                        value={expectedCount}
                        onChange={(e) => setExpectedCount(e.target.value)}
                        placeholder="e.g. 5"
                        className="max-w-xs"
                      />
                    )}
                  </Field>
                ) : (
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field label="Header Object">
                      {() => (
                        <Select
                          value={countObject}
                          onChange={(val) => {
                            setCountObject(val);
                            const flds = getFieldsFor(val);
                            setCountField(flds[0] ?? "");
                          }}
                          options={objectNames.map((o) => ({ value: o, label: o }))}
                          label="Header Object"
                        />
                      )}
                    </Field>

                    <Field label={`Count field in ${countObject}`}>
                      {() => (
                        <Select
                          value={countField}
                          onChange={(val) => setCountField(val)}
                          options={countObjFields.map((f) => ({ value: f, label: f }))}
                          label="Count Field"
                        />
                      )}
                    </Field>
                  </div>
                )}
              </div>
            )}

            {ruleType === "allowed_values" && (
              <div className="grid grid-cols-1 gap-4 rounded-sheet bg-well p-3 sm:grid-cols-2">
                <Field label={`Field in ${targetObject}`}>
                  {() => (
                    <Select
                      value={field}
                      onChange={(val) => setField(val)}
                      options={currentObjFields.map((f) => ({ value: f, label: f }))}
                      label="Field"
                    />
                  )}
                </Field>

                <Field label="Allowed Values (comma separated)" help="e.g. USD, EUR, GBP or kg, pcs, box">
                  {() => (
                    <Input
                      value={allowedValuesText}
                      onChange={(e) => setAllowedValuesText(e.target.value)}
                      placeholder="USD, EUR, GBP"
                    />
                  )}
                </Field>
              </div>
            )}

            {ruleType === "regex" && (
              <div className="grid grid-cols-1 gap-4 rounded-sheet bg-well p-3 sm:grid-cols-2">
                <Field label={`Field in ${targetObject}`}>
                  {() => (
                    <Select
                      value={field}
                      onChange={(val) => setField(val)}
                      options={currentObjFields.map((f) => ({ value: f, label: f }))}
                      label="Field"
                    />
                  )}
                </Field>

                <Field label="Regular Expression Pattern" help="e.g. ^\\d{9}$ or ^[A-Z]{2}-[0-9]+$">
                  {() => (
                    <Input
                      value={regexPattern}
                      onChange={(e) => setRegexPattern(e.target.value)}
                      placeholder="^\\d{9}$"
                      className="font-mono text-meta"
                    />
                  )}
                </Field>
              </div>
            )}

            {ruleType === "unique" && (
              <div className="rounded-sheet bg-well p-3">
                <Field label={`Field in ${targetObject} that must be unique`}>
                  {() => (
                    <Select
                      value={field}
                      onChange={(val) => setField(val)}
                      options={currentObjFields.map((f) => ({ value: f, label: f }))}
                      label="Field"
                      className="max-w-md"
                    />
                  )}
                </Field>
              </div>
            )}

            {ruleType === "time_diff" && (
              <div className="grid grid-cols-1 gap-4 rounded-sheet bg-well p-3 sm:grid-cols-2">
                <Field label="Start Time Field">
                  {() => (
                    <Select
                      value={startField}
                      onChange={(val) => setStartField(val)}
                      options={currentObjFields.map((f) => ({ value: f, label: f }))}
                      label="Start Field"
                    />
                  )}
                </Field>

                <Field label="End Time Field">
                  {() => (
                    <Select
                      value={endField}
                      onChange={(val) => setEndField(val)}
                      options={currentObjFields.map((f) => ({ value: f, label: f }))}
                      label="End Field"
                    />
                  )}
                </Field>

                <Field label="Expected Hours Source">
                  {() => (
                    <Select
                      value={hoursMode}
                      onChange={(val) => setHoursMode(val as "field" | "fixed")}
                      options={[
                        { value: "field", label: "Hours Field in Record" },
                        { value: "fixed", label: "Fixed Hours Value" },
                      ]}
                      label="Hours Source"
                    />
                  )}
                </Field>

                {hoursMode === "field" ? (
                  <Field label="Hours Field in Record">
                    {() => (
                      <Select
                        value={hoursField}
                        onChange={(val) => setHoursField(val)}
                        options={currentObjFields.map((f) => ({ value: f, label: f }))}
                        label="Hours Field"
                      />
                    )}
                  </Field>
                ) : (
                  <Field label="Expected Hours">
                    {() => (
                      <Input
                        type="number"
                        step="0.25"
                        value={expectedHours}
                        onChange={(e) => setExpectedHours(e.target.value)}
                        placeholder="8.0"
                      />
                    )}
                  </Field>
                )}

                <Field label="Tolerance in Hours (default 0.25h / 15m)">
                  {() => (
                    <Input
                      type="number"
                      step="0.05"
                      value={toleranceHours}
                      onChange={(e) => setToleranceHours(e.target.value)}
                      placeholder="0.25"
                    />
                  )}
                </Field>
              </div>
            )}

            {/* Common Description / Error Message */}
            <Field
              label="Plain-English Description (shown in UI on violation)"
              help="If left empty, a descriptive message is automatically generated."
            >
              {() => (
                <Input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="e.g. Line item amount must equal quantity multiplied by unit price"
                />
              )}
            </Field>

            {/* Actions */}
            <div className="mt-2 flex items-center justify-end gap-2 border-t border-border pt-3">
              <Button size="sm" variant="ghost" onClick={() => setEditingIndex(null)}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" onClick={saveRule}>
                <Check className="size-4" /> Save Rule
              </Button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
