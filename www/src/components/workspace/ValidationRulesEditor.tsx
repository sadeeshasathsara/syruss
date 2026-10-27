"use client";

import { useMemo, useState } from "react";
import { Check, Edit3, Plus, SendHorizonal, Trash2, X } from "lucide-react";
import { useAgentChat } from "@/components/chat/ChatProvider";
import { useShell } from "@/components/shell/ShellContext";
import { Button, IconButton } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { fieldName, humanize } from "@/lib/format";
import type { TemplateSchema, ValidationRule } from "@/lib/types";

export const RULE_TYPES = [
  { value: "expression", label: "Calculation / Formula", tone: "accent" as const, desc: "Check calculations or number limits per row" },
  { value: "sum_match", label: "Line Items Sum to Total", tone: "positive" as const, desc: "All rows add up to match the header total" },
  { value: "count_equals", label: "Row Count Check", tone: "neutral" as const, desc: "Rows count must match an expected count" },
  { value: "allowed_values", label: "Allowed Values List", tone: "accent" as const, desc: "Only specific allowed values (e.g. USD, EUR)" },
  { value: "regex", label: "Text Format / Pattern", tone: "neutral" as const, desc: "Must match a specific format or number of digits" },
  { value: "unique", label: "No Duplicate Values", tone: "positive" as const, desc: "Each item must have a unique value in the document" },
  { value: "time_diff", label: "Time Duration", tone: "neutral" as const, desc: "Difference between start and end time matches hours" },
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

/**
 * Intelligent helper that converts plain English into a valid system validation rule.
 */
export function parseNaturalLanguageRule(
  text: string,
  schema: TemplateSchema
): { rule: ValidationRule; plainExplanation: string } | null {
  const query = text.toLowerCase().trim();
  if (!query) return null;

  const objects = schema.objects || [];
  if (objects.length === 0) return null;

  const multiObj = objects.find((o) => o.multiple !== false) ?? objects[0];
  const singleObj = objects.find((o) => o.multiple === false) ?? objects[0];

  const getNumericFields = (obj: (typeof objects)[0]) =>
    obj.fields.filter((f) => (typeof f === "object" && (f.type === "number" || f.type === "integer")) || false).map((f) => fieldName(f));

  const getAllFields = (obj: (typeof objects)[0]) => obj.fields.map((f) => fieldName(f));

  // 1. Line items sum equals header total
  if (
    query.includes("sum") ||
    query.includes("add up") ||
    query.includes("total of lines") ||
    query.includes("lines equal") ||
    query.includes("total lines")
  ) {
    const multiNumerics = getNumericFields(multiObj);
    const singleNumerics = getNumericFields(singleObj);
    const sumField = multiNumerics.find((f) => f.includes("amount") || f.includes("total") || f.includes("price")) ?? multiNumerics[0] ?? "amount";
    const totalField = singleNumerics.find((f) => f.includes("total") || f.includes("amount")) ?? singleNumerics[0] ?? "total";

    return {
      rule: {
        id: `rule_${Date.now().toString(36)}`,
        rule_type: "sum_match",
        object: multiObj.type,
        field: sumField,
        total_object: singleObj.type,
        total_field: totalField,
        tolerance: 0.01,
        description: `Sum of all ${multiObj.type} ${sumField} values must equal ${singleObj.type} ${totalField}`,
      },
      plainExplanation: `Adds up the "${sumField}" from every row in ${multiObj.type} and checks that it matches the "${totalField}" on ${singleObj.type}.`,
    };
  }

  // 2. Line amount = quantity * price
  if (query.includes("quantity") || query.includes("qty") || query.includes("unit price") || query.includes("times") || query.includes("multiplied")) {
    const multiNumerics = getNumericFields(multiObj);
    const amt = multiNumerics.find((f) => f.includes("amount") || f.includes("total")) ?? "amount";
    const qty = multiNumerics.find((f) => f.includes("quantity") || f.includes("qty")) ?? "quantity";
    const price = multiNumerics.find((f) => f.includes("unit_price") || f.includes("price") || f.includes("rate")) ?? "unit_price";

    return {
      rule: {
        id: `rule_${Date.now().toString(36)}`,
        rule_type: "expression",
        object: multiObj.type,
        expression: `abs(${amt} - ${qty} * ${price}) < 0.01`,
        description: `${amt} must equal ${qty} multiplied by ${price}`,
      },
      plainExplanation: `Checks that in every ${multiObj.type} row, "${amt}" equals "${qty}" multiplied by "${price}".`,
    };
  }

  // 3. Shift hours or upper limit
  if (query.includes("cannot exceed") || query.includes("cannot be more than") || query.includes("at most") || query.includes("max")) {
    const matchNumber = query.match(/\d+(\.\d+)?/);
    const limit = matchNumber ? matchNumber[0] : "16";
    const multiFields = getAllFields(multiObj);
    const targetField =
      multiFields.find((f) => f.includes("hour") || f.includes("time") || f.includes("amount") || f.includes("count") || f.includes("qty")) ??
      multiFields[0] ??
      "value";

    return {
      rule: {
        id: `rule_${Date.now().toString(36)}`,
        rule_type: "expression",
        object: multiObj.type,
        expression: `${targetField} <= ${limit}`,
        description: `${targetField} cannot exceed ${limit}`,
      },
      plainExplanation: `Ensures that "${targetField}" on ${multiObj.type} does not exceed ${limit}.`,
    };
  }

  // 4. Non-negative / Positive check
  if (query.includes("not negative") || query.includes("cannot be negative") || query.includes("positive") || query.includes("at least 0")) {
    const multiNumerics = getNumericFields(multiObj);
    const targetField = multiNumerics.find((f) => f.includes("amount") || f.includes("price") || f.includes("total")) ?? multiNumerics[0] ?? "amount";

    return {
      rule: {
        id: `rule_${Date.now().toString(36)}`,
        rule_type: "expression",
        object: multiObj.type,
        expression: `${targetField} >= 0`,
        description: `${targetField} cannot be negative`,
      },
      plainExplanation: `Ensures that "${targetField}" on ${multiObj.type} is zero or positive.`,
    };
  }

  // 5. Allowed values whitelist
  // Catches: "only allow X, Y", "must be one of X, Y", "only X and Y in field",
  //          "only X's and Y's in field", "only [values]", currency/status keywords
  {
    const isAllowedValues =
      query.includes("only allow") ||
      query.includes("must be one of") ||
      query.includes("allowed") ||
      query.includes("currency") ||
      query.includes("status") ||
      // "only X and Y" pattern — broad match for restricted value lists
      /\bonly\b.+\band\b/.test(query) ||
      /\bonly\b.+,/.test(query);

    if (isAllowedValues) {
      const allFields = [...getAllFields(multiObj), ...getAllFields(singleObj)];

      // Try to detect which field the constraint is about from the text itself.
      const fieldFromText = allFields.find((f) => query.includes(f.replace(/_/g, " ")) || query.includes(f));
      const targetField =
        fieldFromText ??
        allFields.find((f) => f.includes("currency") || f.includes("unit") || f.includes("status") || f.includes("type") || f.includes("rank") || f.includes("code")) ??
        allFields[0] ??
        "value";
      const targetObj = getAllFields(multiObj).includes(targetField) ? multiObj : singleObj;

      // Extract the values listed in the text.
      // Strategy: grab ALL_CAPS tokens or words following "only"/"are" that look like labels.
      const capsMatches = text.match(/\b[A-Z]{2,}(?:'s|s)?\b/g) ?? [];
      const cleanedCaps = capsMatches.map((v) => v.replace(/'?s$/i, "").trim()).filter(Boolean);

      // Also try comma/and-separated words after "only" keyword.
      let vals: string[] = [];
      if (cleanedCaps.length >= 2) {
        // Use the extracted uppercase abbreviations (OIC, SSO, USD, EUR, etc.)
        vals = [...new Set(cleanedCaps)];
      } else {
        // Fallback: check known sets
        if (query.includes("usd") || query.includes("eur") || query.includes("gbp")) {
          vals = ["USD", "EUR", "GBP"];
        } else if (query.includes("kg") || query.includes("pcs") || query.includes("box")) {
          vals = ["kg", "pcs", "box"];
        } else {
          vals = ["USD", "EUR", "GBP"];
        }
      }

      return {
        rule: {
          id: `rule_${Date.now().toString(36)}`,
          rule_type: "allowed_values",
          object: targetObj.type,
          field: targetField,
          values: vals,
          description: `${targetField} must be one of: ${vals.join(", ")}`,
        },
        plainExplanation: `Only accepts "${targetField}" if it matches one of: ${vals.join(", ")}.`,
      };
    }
  }

  // 6. Regex format (e.g. 9 digits)
  if (query.includes("digit") || query.includes("pattern") || query.includes("format")) {
    const matchDigits = query.match(/(\d+)\s*digit/);
    const count = matchDigits ? matchDigits[1] : "9";
    const allObjs = [singleObj, multiObj];
    const targetObj = allObjs.find((o) => getAllFields(o).some((f) => f.includes("id") || f.includes("tax") || f.includes("num"))) ?? allObjs[0];
    const targetField = getAllFields(targetObj).find((f) => f.includes("tax") || f.includes("id") || f.includes("number")) ?? getAllFields(targetObj)[0] ?? "tax_id";

    return {
      rule: {
        id: `rule_${Date.now().toString(36)}`,
        rule_type: "regex",
        object: targetObj.type,
        field: targetField,
        pattern: `^\\d{${count}}$`,
        description: `${targetField} must be exactly ${count} digits`,
      },
      plainExplanation: `Verifies that "${targetField}" on ${targetObj.type} contains exactly ${count} numbers.`,
    };
  }

  // 7. Unique
  if (query.includes("unique") || query.includes("duplicate") || query.includes("distinct")) {
    const multiFields = getAllFields(multiObj);
    const targetField = multiFields.find((f) => f.includes("sku") || f.includes("code") || f.includes("item") || f.includes("number")) ?? multiFields[0] ?? "sku";

    return {
      rule: {
        id: `rule_${Date.now().toString(36)}`,
        rule_type: "unique",
        object: multiObj.type,
        field: targetField,
        description: `Each ${targetField} must be unique within the document`,
      },
      plainExplanation: `Makes sure no two rows have the same "${targetField}".`,
    };
  }

  // Fallback: create a pending rule using the raw description so it always appears in the list.
  // The user or agent can then refine it in Manual Form mode.
  return {
    rule: {
      id: `rule_${Date.now().toString(36)}`,
      rule_type: "expression",
      object: multiObj.type,
      expression: "true",
      description: text.trim(),
    },
    plainExplanation: `Recorded your rule as written. Open Manual Form to configure the exact check, or let the agent refine it in chat.`,
  };
}

interface ValidationRulesEditorProps {
  schema: TemplateSchema;
  onChange: (rules: ValidationRule[]) => void;
}

export function ValidationRulesEditor({ schema, onChange }: ValidationRulesEditorProps) {
  const rules = schema.validation_rules ?? [];
  const [editingIndex, setEditingIndex] = useState<number | "new" | null>(null);

  // Tab mode in creator: "natural" (default) or "advanced"
  const [builderMode, setBuilderMode] = useState<"natural" | "advanced">("natural");

  // Natural language prompt state
  const [nlPrompt, setNlPrompt] = useState("");
  const [chatSent, setChatSent] = useState(false);
  const nlResult = useMemo(() => parseNaturalLanguageRule(nlPrompt, schema), [nlPrompt, schema]);

  const { send } = useAgentChat();
  const { setChatOpen } = useShell();

  // Form state for Advanced/Manual mode
  const [targetObject, setTargetObject] = useState<string>("");
  const [ruleType, setRuleType] = useState<string>("expression");
  const [description, setDescription] = useState<string>("");

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
    setBuilderMode("natural");
    setNlPrompt("");
    setTargetObject(defaultObj);
    setRuleType("sum_match");
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
    setBuilderMode("advanced");
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

  const applyNaturalLanguageRule = () => {
    if (!nlResult) return;
    const nextRules = [...rules, nlResult.rule];
    onChange(nextRules);
    setEditingIndex(null);
    setNlPrompt("");
  };

  /**
   * Sends the plain-English rule description to the agent chat and always adds the
   * rule to the list immediately. The local parser now always returns a result (with
   * a fallback description-only rule), so the user sees it right away and can edit
   * it in Manual Form if needed. The agent will also refine it in the chat panel.
   */
  const handleSendToChat = async () => {
    const text = nlPrompt.trim();
    if (!text) return;

    // Always apply locally — the parser always returns a result now.
    applyNaturalLanguageRule();

    await send(`New validation rule: ${text}`, {
      current_page: "template_setup",
      rule_action: "add_validation_rule",
      rule_intent: text,
    });

    // Open the chat panel so the user sees the agent reply.
    setChatOpen(true, "chat");
    setChatSent(true);
    setNlPrompt("");
    // Brief flash, then reset so the user can add another rule.
    setTimeout(() => setChatSent(false), 3000);
    setEditingIndex(null);
  };

  const saveAdvancedRule = () => {
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
            Tell the agent what to check. The system enforces these rules before saving any document.
          </p>
        </div>
        {editingIndex === null && (
          <Button
            size="sm"
            variant="secondary"
            onClick={startNewRule}
            disabled={objectNames.length === 0}
            className="flex-none gap-1.5"
          >
            <Plus className="size-4" /> Add validation rule
          </Button>
        )}
      </div>

      {/* Rules list */}
      {rules.length === 0 && editingIndex === null ? (
        <div className="rounded-card border border-dashed border-border p-6 text-center">
          <p className="text-body text-muted">No custom validation rules yet.</p>
          <p className="mt-1 text-meta text-muted">
            You can tell the agent what to check in plain English, like &quot;Total of line items must match invoice total&quot;.
          </p>
          <Button size="sm" variant="ghost" onClick={startNewRule} disabled={objectNames.length === 0} className="mt-3 gap-1.5">
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

            if (isEditingThis) return null;

            return (
              <div
                key={rule.id ?? idx}
                className="flex flex-col gap-2 rounded-card border border-border bg-card p-3 transition-colors hover:border-control sm:p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={ruleMeta.tone}>{ruleMeta.label}</Pill>
                    <span className="inline-flex items-center rounded-control bg-well px-2 py-0.5 text-meta font-medium text-ink">
                      Applies to: {rule.object}
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
                  <span className="text-meta text-muted">System check:</span>
                  <code className="rounded bg-well px-2 py-0.5 font-mono text-meta text-muted">
                    {formatRuleCode(rule)}
                  </code>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Rule Creator */}
      {editingIndex !== null && (
        <section className="rounded-card border-2 border-accent/40 bg-card p-4 shadow-sm sm:p-5">
          <header className="mb-4 flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
            <div>
              <h4 className="text-section font-semibold text-ink">
                {editingIndex === "new" ? "Add Validation Rule" : "Edit Validation Rule"}
              </h4>
              <p className="text-meta text-muted">
                Describe the rule in plain words, or configure fields directly.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex rounded-control border border-border bg-well p-0.5 text-meta">
                <button
                  type="button"
                  onClick={() => setBuilderMode("natural")}
                  className={`flex items-center gap-1 rounded-sm px-2.5 py-1 transition-colors ${
                    builderMode === "natural" ? "bg-sheet font-medium text-ink shadow-sm" : "text-muted hover:text-ink"
                  }`}
                >
                  Plain English
                </button>
                <button
                  type="button"
                  onClick={() => setBuilderMode("advanced")}
                  className={`rounded-sm px-2.5 py-1 transition-colors ${
                    builderMode === "advanced" ? "bg-sheet font-medium text-ink shadow-sm" : "text-muted hover:text-ink"
                  }`}
                >
                  Manual Form
                </button>
              </div>
              <IconButton aria-label="Cancel" onClick={() => setEditingIndex(null)}>
                <X className="size-4" />
              </IconButton>
            </div>
          </header>

          {/* Natural Language Mode */}
          {builderMode === "natural" && (
            <div className="flex flex-col gap-4">
              <Field
                label="Rule description"
                help="Type what you want to verify across your documents. The agent will translate it into a system check."
              >
                {() => (
                  <div className="flex gap-2">
                    <Input
                      value={nlPrompt}
                      onChange={(e) => setNlPrompt(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          handleSendToChat();
                        }
                      }}
                      placeholder="e.g. Total of all line items must equal the invoice total"
                      className="flex-1 text-body"
                      autoFocus
                    />
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={handleSendToChat}
                      disabled={!nlPrompt.trim()}
                      className="flex-none gap-1.5"
                    >
                      <SendHorizonal className="size-4" />
                      Send
                    </Button>
                  </div>
                )}
              </Field>

              {chatSent && (
                <div className="rounded-card border border-accent/30 bg-accent/5 px-3 py-2 text-meta text-muted">
                  Sent to agent — check the chat panel for the response.
                </div>
              )}



              {/* Translation card */}
              {nlResult ? (
                <div className="flex flex-col gap-3 rounded-card border border-positive/40 bg-positive/5 p-4">
                  <div className="flex items-center gap-2">
                    <Check className="size-4 text-positive" />
                    <span className="text-meta font-semibold uppercase tracking-wider text-positive">
                      Agent understood your rule
                    </span>
                  </div>

                  <p className="text-body font-medium text-ink">{nlResult.rule.description}</p>
                  <p className="text-meta text-muted">{nlResult.plainExplanation}</p>

                  <div className="flex flex-wrap items-center gap-2 border-t border-border/40 pt-2 text-meta text-muted">
                    <span>Target table:</span>
                    <Pill tone="neutral">{nlResult.rule.object}</Pill>
                    <span>Formula:</span>
                    <code className="rounded bg-sheet px-2 py-0.5 font-mono text-ink">
                      {formatRuleCode(nlResult.rule)}
                    </code>
                  </div>

                  <div className="mt-2 flex items-center justify-end gap-2">
                    <Button size="sm" variant="ghost" onClick={() => setEditingIndex(null)}>
                      Cancel
                    </Button>
                    <Button size="sm" variant="primary" onClick={handleSendToChat}>
                      <Check className="size-4" /> Add this rule
                    </Button>
                  </div>
                </div>
              ) : nlPrompt.trim().length > 0 ? (
                <div className="rounded-card border border-border bg-well p-3 text-meta text-muted">
                  <p>
                    Keep typing or describe your rule like: &quot;Total of lines must equal total&quot; or &quot;Amount must be positive&quot;.
                  </p>
                </div>
              ) : null}
            </div>
          )}

          {/* Manual Form Mode */}
          {builderMode === "advanced" && (
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Table / Object to Check">
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

                <Field label="Check Type">
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

              {ruleType === "sum_match" && (
                <div className="grid grid-cols-1 gap-4 rounded-sheet bg-well p-3 sm:grid-cols-2">
                  <Field label={`Field to sum in ${targetObject}`}>
                    {() => (
                      <Select
                        value={field}
                        onChange={(val) => setField(val)}
                        options={currentObjFields.map((f) => ({ value: f, label: f }))}
                        label="Field to Sum"
                      />
                    )}
                  </Field>

                  <Field label="Header / Summary Table">
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

                  <Field label="Allowed Rounding Margin (default 0.01)">
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

              {ruleType === "expression" && (
                <div className="flex flex-col gap-2 rounded-sheet bg-well p-3">
                  <Field label="Calculation or Limit Condition">
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
                      <span className="text-meta text-muted">Click field to insert:</span>
                      {currentObjFields.map((fName) => (
                        <button
                          type="button"
                          key={fName}
                          onClick={() => setExpression((prev) => (prev ? `${prev} ${fName}` : fName))}
                          className="rounded bg-sheet px-2 py-0.5 font-mono text-meta text-ink border border-border hover:border-accent"
                        >
                          +{fName}
                        </button>
                      ))}
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
                  <Field label="Allowed Values (separated by commas)" help="e.g. USD, EUR, GBP">
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
                  <Field label="Format / Digits Pattern" help="e.g. ^\\d{9}$ for 9 digits">
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
                  <Field label={`Field that must have no duplicates`}>
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
                      <span>Fixed row count</span>
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="radio"
                        checked={countMode === "field"}
                        onChange={() => setCountMode("field")}
                        className="accent-[var(--ink)]"
                      />
                      <span>Match count field in header table</span>
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
                      <Field label="Header Table">
                        {() => (
                          <Select
                            value={countObject}
                            onChange={(val) => {
                              setCountObject(val);
                              const flds = getFieldsFor(val);
                              setCountField(flds[0] ?? "");
                            }}
                            options={objectNames.map((o) => ({ value: o, label: o }))}
                            label="Header Table"
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
                </div>
              )}

              <Field label="Explanation (shown to users if violated)">
                {() => (
                  <Input
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="e.g. Line item amount must equal quantity multiplied by unit price"
                  />
                )}
              </Field>

              <div className="mt-2 flex items-center justify-end gap-2 border-t border-border pt-3">
                <Button size="sm" variant="ghost" onClick={() => setEditingIndex(null)}>
                  Cancel
                </Button>
                <Button size="sm" variant="primary" onClick={saveAdvancedRule}>
                  <Check className="size-4" /> Save Rule
                </Button>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
