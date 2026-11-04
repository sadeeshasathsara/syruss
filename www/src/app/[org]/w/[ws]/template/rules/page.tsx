"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import Link from "next/link";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { DryRun } from "@/components/rules/DryRun";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { ago, humanize } from "@/lib/format";
import type { Rule, RuleStatus } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const SECTIONS: { status: RuleStatus; title: string; note: string }[] = [
  { status: "suggested", title: "Suggested by Syruss", note: "Nothing is checked until you add it." },
  { status: "broken", title: "Stopped", note: "The template changed and these no longer fit it. Fix them to turn them back on." },
  { status: "active", title: "Active", note: "Every record is checked with these after its document is read." },
  { status: "disabled", title: "Turned off", note: "" },
];

/** The checks every record goes through after its document is read, and the ones Syruss suggests. */
export default function RulesPage() {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const { can } = useAccess(wid);
  const manage = can("template.publish");
  const rules = useQuery({ queryKey: ["rules", wid], queryFn: () => api.rules.list(wid) });
  const all = rules.data?.rules ?? [];
  const checks = rules.data?.template_checks ?? [];

  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <PageHeader
        title="Rules"
        subtitle="Checks that run on every record after its document is read. A failing rule stops the record and sends the document to review."
        actions={manage && <Button asChild variant="primary"><Link href={href("template/rules/new")}>New rule</Link></Button>}
      />
      {rules.isError && <ErrorNote message="Couldn't load the rules." onRetry={() => rules.refetch()} />}
      {rules.isLoading && <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-16 w-full" />)}</div>}
      {rules.data && all.length === 0 && checks.length === 0 && (
        <Empty title="No rules yet" action={manage && <Button asChild variant="secondary"><Link href={href("template/rules/new")}>Add the first rule</Link></Button>}>
          Rules catch misreads: lines that don&apos;t add up to the total, a shift longer than a day, an ID in the wrong format.
          Describe one in plain words and Syruss writes it, or write it yourself.
        </Empty>
      )}

      {SECTIONS.map((section) => {
        const list = all.filter((r) => r.status === section.status);
        if (!list.length) return null;
        return (
          <section key={section.status} className="flex flex-col gap-3">
            <div>
              <h2 className="text-section font-semibold text-ink">{section.title} <span className="num text-meta text-muted">{list.length}</span></h2>
              {section.note && <p className="text-body text-muted">{section.note}</p>}
            </div>
            <ul className="flex flex-col gap-3">{list.map((r) => <RuleRow key={r.id} rule={r} manage={manage} />)}</ul>
          </section>
        );
      })}

      {checks.length > 0 && (
        <Panel title="From the template" aside="Change these with a new template version">
          <ul className="flex flex-col gap-1">
            {checks.map((c, i) => (
              <li key={c.id ?? i} className="text-body text-ink">{c.description ?? `${humanize(c.rule_type)} on ${c.object}`}</li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function RuleRow({ rule, manage }: { rule: Rule; manage: boolean }) {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["rules", wid] });
  const add = useMutation({ mutationFn: () => api.rules.accept(wid, rule.id), onSuccess: refresh });
  const dismiss = useMutation({ mutationFn: () => api.rules.dismiss(wid, rule.id), onSuccess: refresh });
  const toggle = useMutation({ mutationFn: () => api.rules.update(wid, rule.id, { status: rule.status === "active" ? "disabled" : "active" }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.rules.remove(wid, rule.id), onSuccess: refresh });
  const error = [add, dismiss, toggle, remove].find((m) => m.error)?.error;
  const busy = [add, dismiss, toggle, remove].some((m) => m.isPending);
  const scope = rule.scope === "document" ? "Each document" : `Each ${humanize(rule.scope).toLowerCase()} row`;

  return (
    <li className="rounded-card border border-border bg-sheet p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-body font-semibold text-ink">{rule.name}</h3>
            <Pill tone={rule.severity === "error" ? "negative" : "warning"}>{rule.severity === "error" ? "Stops the record" : "Warning"}</Pill>
            {rule.created_by_kind === "agent" && <Pill tone="accent"><Sparkles aria-hidden className="mr-1 inline size-3" />Syruss</Pill>}
          </div>
          <p className="mt-0.5 text-meta text-muted">{scope} · {rule.status === "suggested" ? "suggested" : "updated"} {ago(rule.updated_at).toLowerCase()}</p>
        </div>
        {manage && (
          <div className="flex flex-wrap gap-2">
            {rule.status === "suggested" ? (
              <>
                <Button variant="primary" size="sm" disabled={busy} onClick={() => add.mutate()}>Add rule</Button>
                <Button asChild variant="secondary" size="sm"><Link href={href(`template/rules/${rule.id}`)}>Edit first</Link></Button>
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => dismiss.mutate()}>No thanks</Button>
              </>
            ) : (
              <>
                <Button asChild variant="secondary" size="sm"><Link href={href(`template/rules/${rule.id}`)}>{rule.status === "broken" ? "Fix" : "Edit"}</Link></Button>
                {rule.status !== "broken" && (
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => toggle.mutate()}>{rule.status === "active" ? "Turn off" : "Turn on"}</Button>
                )}
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => { if (confirm(`Delete the rule “${rule.name}”?`)) remove.mutate(); }}>Delete</Button>
              </>
            )}
          </div>
        )}
      </div>
      <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink scrollbar-thin">
        {rule.when ? `when ${rule.when}\n` : ""}{rule.check}
      </pre>
      {rule.broken_reason && <p className="mt-2 text-body text-negative">{rule.broken_reason}</p>}
      {(rule.rationale || rule.description) && <p className="mt-2 text-body text-ink">{rule.rationale || rule.description}</p>}
      {rule.last_dry_run && rule.status !== "broken" && <div className="mt-2"><DryRun run={rule.last_dry_run} compact /></div>}
      {error && <p className="mt-2 text-body text-negative">{error.message}</p>}
    </li>
  );
}
