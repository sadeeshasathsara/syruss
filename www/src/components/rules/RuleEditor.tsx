"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { useAccess } from "@/lib/access";
import { api, ApiError } from "@/lib/api";
import { cn, humanize } from "@/lib/format";
import type { Rule, RuleCheckResult, RuleInput, RuleProblem } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { DryRun } from "./DryRun";

const EMPTY: RuleInput = { name: "", description: "", scope: "document", check: "", when: "", severity: "error", message: "" };

/** Waits until typing pauses before the value changes, so the live check isn't sent on every key. */
function useSettled<T>(value: T, ms = 400): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

/** The source with the part the compiler stopped at marked, so the person sees exactly where. */
function Marked({ source, problem }: { source: string; problem: RuleProblem }) {
  const text = source.replace(/\s+/g, " ").trim();
  if (problem.col === null || problem.col > text.length) return null;
  const start = problem.col;
  const end = Math.max(start + 1, Math.min(problem.end_col ?? start + 1, text.length));
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink scrollbar-thin">
      {text.slice(0, start)}
      <mark className="rounded-sm bg-[var(--pill-negative-bg)] px-0.5 text-[var(--pill-negative-ink)] underline decoration-wavy underline-offset-4">
        {text.slice(start, end) || " "}
      </mark>
      {text.slice(end)}
    </pre>
  );
}

function ProblemNote({ problem, source }: { problem: RuleProblem; source: string }) {
  return (
    <div className="flex flex-col gap-2 rounded-control border border-[var(--pill-negative-bg)] p-3" role="alert">
      <p className="text-body font-medium text-negative">{problem.message}</p>
      {(problem.part === "check" || problem.part === "when") && <Marked source={source} problem={problem} />}
      {problem.explanation && <p className="text-body text-ink">{problem.explanation}</p>}
      {problem.hint && <p className="text-body text-muted">{problem.hint}</p>}
    </div>
  );
}

/**
 * Write a rule yourself: what it checks, on what, and how strict. It is compiled against the template as you type and
 * tried on the saved records, and it can only be saved once it compiles.
 */
export function RuleEditor({ initial }: { initial?: Rule }) {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const router = useRouter();
  const queryClient = useQueryClient();
  const { can } = useAccess(wid);
  const [rule, setRule] = useState<RuleInput>(initial ? {
    name: initial.name, description: initial.description, scope: initial.scope, check: initial.check, when: initial.when,
    severity: initial.severity, message: initial.message,
  } : EMPTY);
  const [showWhen, setShowWhen] = useState(!!initial?.when);
  const checkRef = useRef<HTMLTextAreaElement>(null);
  const set = (patch: Partial<RuleInput>) => setRule((r) => ({ ...r, ...patch }));

  const language = useQuery({ queryKey: ["rules", wid, "language"], queryFn: () => api.rules.language(wid) });
  const draft = useSettled({ scope: rule.scope, check: rule.check, when: showWhen ? rule.when : "", severity: rule.severity });
  const check = useQuery<RuleCheckResult>({
    queryKey: ["rules", wid, "check", draft],
    queryFn: () => api.rules.check(wid, draft),
    enabled: !!draft.check.trim(),
    placeholderData: (prev) => prev,
  });

  const save = useMutation({
    mutationFn: () => {
      const body = { ...rule, when: showWhen ? rule.when : "" };
      return initial ? api.rules.update(wid, initial.id, body) : api.rules.create(wid, body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules", wid] });
      router.push(href("template/rules"));
    },
  });

  const objects = language.data?.objects ?? [];
  const scoped = objects.find((o) => o.type === rule.scope);
  const singles = objects.filter((o) => !o.multiple);
  const many = objects.filter((o) => o.multiple);
  // Placeholders made from this template's own fields, so the example always fits the documents at hand.
  const numeric = (o?: (typeof objects)[number]) => o?.fields.find((f) => f.type === "number" || f.type === "integer");
  const singleWithNumber = singles.find((o) => numeric(o));
  const checkExample = scoped
    ? (numeric(scoped) ? `${numeric(scoped)!.name} >= 0` : scoped.fields[0] ? `not isempty(${scoped.fields[0].name})` : "")
    : singleWithNumber ? `${singleWithNumber.type}.${numeric(singleWithNumber)!.name} >= 0` : many[0] ? `count(${many[0].type}) > 0` : "";
  const whenExample = scoped?.fields[0] ? `not isempty(${scoped.fields[0].name})` : "";
  const result = check.data;
  const problem = result?.problems[0];
  const fresh = draft.check === rule.check && draft.scope === rule.scope;
  const ready = !!rule.name.trim() && !!rule.check.trim() && fresh && result?.ok === true;
  const saveProblem = save.error instanceof ApiError
    ? ((save.error.detail as { problems?: RuleProblem[] } | undefined)?.problems?.[0]) : undefined;

  /** Puts a field or function where the cursor is in the check. */
  const insert = (text: string) => {
    const el = checkRef.current;
    const at = el ? el.selectionStart : rule.check.length;
    const end = el ? el.selectionEnd : at;
    const next = rule.check.slice(0, at) + text + rule.check.slice(end);
    set({ check: next });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(at + text.length, at + text.length);
    });
  };

  if (!can("template.publish")) {
    return <p className="text-body text-muted">Your role can see rules but not change them.</p>;
  }

  return (
    <div className="grid grid-cols-1 items-start gap-5 @4xl:grid-cols-[minmax(0,1fr)_18rem]">
      <form className="flex min-w-0 flex-col gap-5" onSubmit={(e) => { e.preventDefault(); if (ready) save.mutate(); }}>
        <Field label="Name" help="People see it when the rule fails, and in the list of rules.">
          {(id) => <Input id={id} value={rule.name} maxLength={120} onChange={(e) => set({ name: e.target.value })} placeholder="What the rule checks, in your words" />}
        </Field>

        <Field label="What it checks" help={rule.scope === "document" ? "Once per document." : `Each ${humanize(rule.scope).toLowerCase()} row on its own: its fields are written by name.`}>
          {(id) => (
            <Select id={id} value={rule.scope} onChange={(scope) => set({ scope })} options={[
              { value: "document", label: "The whole document" },
              ...objects.map((o) => ({ value: o.type, label: o.multiple ? `Each ${humanize(o.type).toLowerCase()} row` : humanize(o.type) })),
            ]} />
          )}
        </Field>

        <Field label="The check" help="True when the record is right. Pick fields and functions from the list to add them.">
          {(id) => (
            <Textarea
              id={id} ref={checkRef} value={rule.check} rows={3} spellCheck={false} maxLength={600}
              onChange={(e) => set({ check: e.target.value })}
              className={cn("font-mono", problem?.part === "check" && fresh && "border-negative")}
              placeholder={checkExample}
            />
          )}
        </Field>

        {showWhen ? (
          <Field label="Only check when" help="Rows (or documents) where this is false are skipped.">
            {(id) => (
              <div className="flex gap-2">
                <Input id={id} value={rule.when} spellCheck={false} maxLength={600} className="font-mono" onChange={(e) => set({ when: e.target.value })}
                       placeholder={whenExample} />
                <Button type="button" variant="ghost" onClick={() => { setShowWhen(false); set({ when: "" }); }}>Remove</Button>
              </div>
            )}
          </Field>
        ) : (
          <div><Button type="button" variant="ghost" size="sm" onClick={() => setShowWhen(true)}>Only check when…</Button></div>
        )}

        <div className="grid grid-cols-1 gap-5 @2xl:grid-cols-2">
          <Field label="When it fails">
            {(id) => (
              <Select id={id} value={rule.severity ?? "error"} onChange={(severity) => set({ severity })} options={[
                { value: "error", label: "Stop the record: send it to review" },
                { value: "warning", label: "Warn only" },
              ]} />
            )}
          </Field>
          <Field label="Message" help="What people read when it fails.">
            {(id) => <Input id={id} value={rule.message} maxLength={500} onChange={(e) => set({ message: e.target.value })} placeholder="What is wrong, in your words" />}
          </Field>
        </div>

        <Field label="Description" help="Optional: why the rule exists.">
          {(id) => <Textarea id={id} value={rule.description} rows={2} maxLength={2000} onChange={(e) => set({ description: e.target.value })} />}
        </Field>

        <div aria-live="polite" className="flex flex-col gap-2">
          {!rule.check.trim() ? null : !fresh || check.isFetching && !result ? (
            <p className="text-body text-muted">Checking…</p>
          ) : problem ? (
            <ProblemNote problem={problem} source={problem.part === "when" ? rule.when ?? "" : rule.check} />
          ) : result?.ok && result.dry_run ? (
            <DryRun run={result.dry_run} />
          ) : check.isError ? (
            <p className="text-body text-negative">{check.error.message}</p>
          ) : null}
        </div>

        {save.error && (saveProblem
          ? <ProblemNote problem={saveProblem} source={saveProblem.part === "when" ? rule.when ?? "" : rule.check} />
          : <p className="text-body text-negative">{save.error.message}</p>)}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={!ready || save.isPending}>
            {save.isPending ? "Saving…" : initial ? "Save changes" : "Save rule"}
          </Button>
          <Button asChild variant="ghost"><Link href={href("template/rules")}>Cancel</Link></Button>
        </div>
      </form>

      <Panel title="Fields and functions" className="@4xl:sticky @4xl:top-4" bodyClassName="flex max-h-[70vh] flex-col gap-4 overflow-y-auto scrollbar-thin">
        <p className="text-meta text-muted">Click to put it in the check.</p>
        {scoped && (
          <Palette title={`This ${humanize(scoped.type).toLowerCase()} row`} items={scoped.fields.map((f) => ({
            text: f.name, note: f.resolves_to ? `${f.type}, refers to ${f.resolves_to}` : f.type,
          }))} onPick={insert} />
        )}
        {singles.map((o) => (
          <Palette key={o.type} title={humanize(o.type)} items={o.fields.map((f) => ({ text: `${o.type}.${f.name}`, note: f.type }))} onPick={insert} />
        ))}
        {many.filter((o) => o.type !== rule.scope).map((o) => (
          <Palette key={o.type} title={`${humanize(o.type)} (many rows)`} items={[
            { text: `count(${o.type})`, note: "rows" },
            ...o.fields.map((f) => ({ text: `${o.type}.${f.name}`, note: `${f.type}, inside sum(), avg() ...` })),
          ]} onPick={insert} />
        ))}
        {language.data && (
          <>
            <Palette title="Across rows" items={language.data.aggregates.map((a) => ({ text: `${a.name}(`, note: a.usage }))} onPick={insert} />
            <Palette title="Functions" items={language.data.functions.map((f) => ({ text: `${f.name}(`, note: f.usage }))} onPick={insert} />
            <div>
              <h3 className="mb-1 text-meta font-medium text-muted">Operators</h3>
              <ul className="flex flex-col gap-0.5">{language.data.operators.map((o) => <li key={o} className="font-mono text-meta text-ink">{o}</li>)}</ul>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}

function Palette({ title, items, onPick }: { title: string; items: { text: string; note: string }[]; onPick: (text: string) => void }) {
  return (
    <div>
      <h3 className="mb-1 text-meta font-medium text-muted">{title}</h3>
      <ul className="flex flex-col">
        {items.map((i) => (
          <li key={i.text}>
            <button type="button" onClick={() => onPick(i.text)} title={i.note}
                    className="-mx-1.5 flex w-[calc(100%+12px)] items-baseline gap-2 rounded-control px-1.5 py-1 text-left hover:bg-raised">
              <span className="min-w-0 break-all font-mono text-meta text-ink">{i.text}</span>
              <span className="ml-auto flex-none truncate text-meta text-faint">{i.note.length > 22 ? "" : i.note}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
