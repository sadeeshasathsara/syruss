"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { Pill } from "@/components/ui/Pill";
import { cn } from "@/lib/format";
import { duration, tokens } from "@/lib/runs";
import type { RunPrompt, RunStep } from "@/lib/types";

const fmt = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));
const took = (s: RunStep) => (s.step_started_at && s.step_completed_at ? new Date(s.step_completed_at).getTime() - new Date(s.step_started_at).getTime() : null);
const stepTokens = (s: RunStep) => s.usage?.total_tokens ?? ((s.usage?.input_tokens ?? 0) + (s.usage?.output_tokens ?? 0) || undefined);

function Block({ label, children, open = false }: { label: React.ReactNode; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} className="group rounded-control border border-border bg-sheet">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-meta font-medium text-muted hover:text-ink">
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />{label}
      </summary>
      <div className="border-t border-border p-2.5">{children}</div>
    </details>
  );
}

function Text({ children, mono = false }: { children: string; mono?: boolean }) {
  return (
    <pre className={cn("max-h-96 overflow-auto whitespace-pre-wrap text-meta text-ink scrollbar-thin [overflow-wrap:anywhere]", !mono && "font-sans text-body")}>
      {children}
    </pre>
  );
}

/** The whole message list the model got at step `n`: the prompt, then every earlier step's answer, tool results and additions. */
function FullPrompt({ prompt, steps, n }: { prompt: RunPrompt | null; steps: RunStep[]; n: number }) {
  const before = steps.filter((s) => s.step_number < n);
  const current = steps.find((s) => s.step_number === n);
  return (
    <ol className="flex flex-col gap-2">
      <li><Block label="System (the agent's instructions)"><Text>{prompt?.system ?? "Not recorded for this run."}</Text></Block></li>
      <li><Block label="Briefing (this run's task)" open><Text>{prompt?.briefing ?? "Not recorded for this run."}</Text></Block></li>
      {before.map((s) => (
        <li key={s.step_number} className="flex flex-col gap-2">
          {s.added?.map((a, i) => <Block key={`a${i}`} label={`Added before step ${s.step_number}`}><Text>{a}</Text></Block>)}
          <Block label={`Its answer at step ${s.step_number}`}><Text>{s.thought || (s.tool_calls.length ? `Called ${s.tool_calls.map((c) => c.tool_name).join(", ")}` : "(nothing)")}</Text></Block>
          {s.tool_calls.map((c, i) => (
            <Block key={i} label={`Result of ${c.tool_name}`}><Text mono>{c.observation ?? ""}</Text></Block>
          ))}
        </li>
      ))}
      {current?.added?.map((a, i) => <li key={`c${i}`}><Block label="Added before this step"><Text>{a}</Text></Block></li>)}
    </ol>
  );
}

function StepBody({ step: s, prompt, steps }: { step: RunStep; prompt: RunPrompt | null; steps: RunStep[] }) {
  const [full, setFull] = useState(false);
  const previous = steps.find((p) => p.step_number === s.step_number - 1);
  const u = s.usage ?? {};
  return (
    <div className="flex flex-col gap-4 pb-3 pl-6 pr-1 pt-1">
      <section className="flex flex-col gap-2">
        <h4 className="text-meta font-semibold uppercase tracking-wide text-faint">Sent to the model</h4>
        {full ? <FullPrompt prompt={prompt} steps={steps} n={s.step_number} /> : s.step_number === 1 ? (
          <div className="flex flex-col gap-2">
            <Block label="System (the agent's instructions)"><Text>{prompt?.system ?? "Not recorded for this run."}</Text></Block>
            <Block label="Briefing (this run's task)" open><Text>{prompt?.briefing ?? "Not recorded for this run."}</Text></Block>
            {!!prompt?.tools.length && <p className="text-meta text-muted">Tools it could use: {prompt.tools.join(", ")}</p>}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-meta text-muted">Everything sent before, plus what step {s.step_number - 1} brought back:</p>
            {previous?.tool_calls.map((c, i) => <Block key={i} label={`Result of ${c.tool_name}`}><Text mono>{c.observation ?? ""}</Text></Block>)}
            {!previous?.tool_calls.length && <p className="text-meta text-muted">No tool results came back from step {s.step_number - 1}.</p>}
          </div>
        )}
        {!full && s.added?.map((a, i) => <Block key={i} label="Added before this step" open><Text>{a}</Text></Block>)}
        <button type="button" onClick={() => setFull((v) => !v)} className="self-start text-meta text-ink underline-offset-2 hover:underline">
          {full ? "Show only what was new" : "Show the full prompt"}
        </button>
      </section>

      <section className="flex flex-col gap-2">
        <h4 className="text-meta font-semibold uppercase tracking-wide text-faint">The model answered</h4>
        {s.reasoning && <Block label="Its thinking"><Text>{s.reasoning}</Text></Block>}
        {s.thought ? <Text>{s.thought}</Text> : <p className="text-meta text-muted">{s.tool_calls.length ? "No words: it went straight to its tools." : "Nothing."}</p>}
        <p className="num text-meta text-muted">
          {u.input_tokens != null ? `${tokens(u.input_tokens)} tokens in · ${tokens(u.output_tokens ?? 0)} out` : "Tokens weren't recorded for this step."}
          {Object.entries(u).filter(([k, v]) => !["input_tokens", "output_tokens", "total_tokens"].includes(k) && typeof v === "number" && v)
            .map(([k, v]) => ` · ${k.replace(/_/g, " ")} ${tokens(v as number)}`).join("")}
        </p>
      </section>

      {s.tool_calls.length > 0 && (
        <section className="flex flex-col gap-2">
          <h4 className="text-meta font-semibold uppercase tracking-wide text-faint">Tools it ran</h4>
          {s.tool_calls.map((c, i) => (
            <div key={i} className="flex flex-col gap-1.5 rounded-control border border-border p-2.5">
              <p className="flex flex-wrap items-center gap-2 text-body">
                <span className={cn("font-medium", c.status === "error" ? "text-[var(--pill-warning-ink)]" : "text-ink")}>{c.tool_name}</span>
                <Pill tone={c.status === "error" ? "warning" : "positive"}>{c.status === "error" ? "Refused or failed" : "Done"}</Pill>
                <span className="num text-meta text-muted">{duration(c.duration_ms)}</span>
              </p>
              <div className="grid grid-cols-1 gap-2 @4xl:grid-cols-2">
                <Block label="What it sent" open><Text mono>{fmt(c.arguments ?? {})}</Text></Block>
                <Block label="What came back" open><Text mono>{c.observation ?? ""}</Text></Block>
              </div>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

/**
 * A run's steps. Each opens to show what was sent to the model, what it answered (and its thinking, when the provider
 * returns it) and the tools it ran, with their inputs and results; opening one closes the one open before.
 */
export function RunSteps({ steps, prompt }: { steps: RunStep[]; prompt: RunPrompt | null }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <ol className="flex flex-col divide-y divide-rule">
      {steps.map((s) => {
        const isOpen = open === s.step_number;
        const t = stepTokens(s);
        return (
          <li key={s.step_number}>
            <button type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : s.step_number)}
              className="flex w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-control px-1 py-2.5 text-left hover:bg-raised">
              <ChevronRight className={cn("size-4 flex-none text-muted transition-transform", isOpen && "rotate-90")} aria-hidden />
              <span className="num text-body font-medium text-ink">Step {s.step_number}</span>
              <span className="min-w-0 flex-1 truncate text-body text-muted">
                {s.tool_calls.length ? s.tool_calls.map((c) => c.tool_name).join(", ") : s.thought ? s.thought.slice(0, 120) : "No answer"}
              </span>
              {s.tool_calls.some((c) => c.status === "error") && <Pill tone="warning">Refused</Pill>}
              <span className="num text-meta text-muted">{t ? `${tokens(t)} tokens · ` : ""}{duration(took(s))}</span>
            </button>
            {isOpen && <StepBody step={s} prompt={prompt} steps={steps} />}
          </li>
        );
      })}
    </ol>
  );
}
