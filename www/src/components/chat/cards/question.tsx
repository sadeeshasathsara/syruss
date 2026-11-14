"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import type { AgentQuestion, FlagAnswer } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { useAgentChat } from "../ChatProvider";

/** The rows the agent read, as a table: numbered, in the order they appear on the page. */
export function DataTableView({ table }: { table: NonNullable<AgentQuestion["data_table"]> }) {
  return (
    <figure className="mt-2 min-w-0">
      {table.title && <figcaption className="mb-1 text-meta font-medium text-muted">{table.title}, {table.rows.length} {table.rows.length === 1 ? "row" : "rows"}</figcaption>}
      <div className="max-h-80 overflow-auto rounded-control border border-border bg-sheet scrollbar-thin">
        <table className="w-full border-collapse text-body">
          <thead className="sticky top-0 bg-raised text-left text-meta text-muted">
            <tr>
              <th className="w-8 px-2 py-1.5 text-right font-medium">#</th>
              {table.columns.map((c, i) => <th key={i} className="px-2 py-1.5 font-medium">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className="border-t border-rule">
                <td className="num px-2 py-1.5 text-right text-meta text-faint">{i + 1}</td>
                {table.columns.map((_, j) => <td key={j} className="px-2 py-1.5 text-ink [overflow-wrap:anywhere]">{r[j] ?? ""}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

export function QuestionCard({ question }: { question: AgentQuestion }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [otherOn, setOtherOn] = useState(false);
  const kind = question.answer_type ?? (question.options?.length ? "single" : "text");
  const { openConversation } = useAgentChat();
  const answer = useMutation({
    mutationFn: (value: FlagAnswer) => api.questions.answer(workspace.id, question.id, value),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["questions", workspace.id] });
      queryClient.invalidateQueries({ queryKey: ["workspace"] });
      if (res.conversation_id) openConversation(res.conversation_id);
    },
  });
  const toggle = (option: string) => {
    if (kind === "single") {
      setPicked([option]);
      setOtherOn(false);
    } else {
      setPicked((p) => (p.includes(option) ? p.filter((x) => x !== option) : [...p, option]));
    }
  };
  const other = otherOn ? text.trim() : "";
  const ready = kind === "text" ? !!text.trim() : picked.length > 0 || !!other;
  const submit = () => {
    if (!ready) return;
    answer.mutate(kind === "text" ? { answer: text.trim() } : { choices: picked, other: other || undefined });
  };
  const dismiss = useMutation({
    mutationFn: () => api.questions.dismiss(workspace.id, question.id, "Not relevant"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["questions", workspace.id] }),
  });
  const answered = answer.data ?? (question.status !== "OPEN" ? question : undefined);

  return (
    <div className={cn("rounded-card px-3 py-3 transition-colors [box-shadow:var(--shadow-card)]", answered ? "bg-card/60" : "bg-card border border-control")}>
      <div className="flex items-center gap-2">
        <Pill tone="info">{kind === "text" ? "Question for you" : "Decision for you"}</Pill>
        {question.priority === "high" && <span className="text-meta text-muted">Work is waiting on this</span>}
      </div>
      <p className="mt-2 text-body font-medium text-ink">{question.question}</p>
      {question.context && <p className="mt-1 text-body text-muted">{question.context}</p>}
      {question.data_table && question.data_table.rows.length > 0 && <DataTableView table={question.data_table} />}
      {answered ? (
        <div className="mt-2 flex flex-col gap-2">
          <p className="text-body text-ink">
            {answered.status === "DISMISSED" ? "Dismissed." : <>Answered: <span className="font-medium">{answered.answer}</span></>}
          </p>
          {answered.status === "ANSWERED" && answered.conversation_id && (
            answered.conversation_status === "resolved" ? (
              <p className="text-meta text-muted">Syruss resolved this.</p>
            ) : (
              <div className="flex flex-wrap items-center gap-2 rounded-control bg-[var(--pill-info-bg)] px-3 py-2 text-body text-[var(--pill-info-ink)]">
                <span className="min-w-0 flex-1">Pending: Syruss is working on your answer in the chat, and closes this when nothing is left.</span>
                <Button size="sm" variant="secondary" onClick={() => openConversation(answered.conversation_id!)}>Open the chat</Button>
              </div>
            )
          )}
        </div>
      ) : (
        <>
        <form
          className="mt-3 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {kind !== "text" && (
            <fieldset className="flex flex-col gap-1">
              <legend className="sr-only">{kind === "single" ? "Pick one" : "Pick any that apply"}</legend>
              {[...question.options, "Other"].map((o) => {
                const isOther = o === "Other";
                const checked = isOther ? otherOn : picked.includes(o);
                return (
                  <label key={o} className="flex cursor-pointer items-center gap-2 rounded-control px-1.5 py-1 text-body text-ink hover:bg-raised">
                    <input
                      type={kind === "single" ? "radio" : "checkbox"}
                      name={`flag-${question.id}`}
                      className="size-4 cursor-pointer accent-[var(--ink)]"
                      checked={checked}
                      onChange={() => {
                        if (!isOther) return toggle(o);
                        if (kind === "single") setPicked([]);
                        setOtherOn(kind === "single" ? true : !otherOn);
                      }}
                    />
                    {isOther ? "Other" : o}
                  </label>
                );
              })}
            </fieldset>
          )}
          {(kind === "text" || otherOn) && (
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={kind === "text" ? "Your answer" : "Write your own answer"}
              aria-label={kind === "text" ? "Your answer" : "Other answer"}
              autoFocus={otherOn}
            />
          )}
          <div>
            <Button type="submit" variant="primary" size="sm" disabled={!ready || answer.isPending}>
              {answer.isPending ? "Sending…" : "Answer"}
            </Button>
          </div>
        </form>
          <button type="button" onClick={() => dismiss.mutate()} className="mt-2 text-meta text-muted hover:text-ink">
            Not relevant
          </button>
          {(answer.error || dismiss.error) && <p className="mt-1 text-meta text-negative">{(answer.error ?? dismiss.error)?.message}</p>}
        </>
      )}
    </div>
  );
}
