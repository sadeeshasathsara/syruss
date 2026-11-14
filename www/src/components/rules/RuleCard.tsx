"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { useAccess } from "@/lib/access";
import { api, ApiError } from "@/lib/api";
import { humanize } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";
import { DryRun } from "./DryRun";

/** A rule Syruss suggested, where it suggested it (the chat): what it checks, and Add or No thanks. */
export function RuleCard({ ruleId }: { ruleId: string }) {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const { can } = useAccess(wid);
  const queryClient = useQueryClient();
  const rule = useQuery({
    queryKey: ["rules", wid, "one", ruleId],
    queryFn: () => api.rules.get(wid, ruleId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["rules", wid] });
  const add = useMutation({ mutationFn: () => api.rules.accept(wid, ruleId), onSuccess: refresh });
  const dismiss = useMutation({ mutationFn: () => api.rules.dismiss(wid, ruleId), onSuccess: refresh });

  if (rule.error instanceof ApiError && rule.error.status === 404) {
    return <p className="text-meta text-muted">This suggestion was turned down.</p>;
  }
  if (!rule.data) return null;
  const r = rule.data;
  const scope = r.scope === "document" ? "Each document" : `Each ${humanize(r.scope).toLowerCase()} row`;
  return (
    <div className="flex flex-col gap-2 rounded-card border border-border bg-sheet p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body font-semibold text-ink">{r.name}</span>
        <Pill tone={r.severity === "error" ? "negative" : "warning"}>{r.severity === "error" ? "Stops the record" : "Warning"}</Pill>
        {r.status === "active" && <Pill tone="positive">Added</Pill>}
        {r.status === "disabled" && <Pill tone="neutral">Turned off</Pill>}
        {r.status === "broken" && <Pill tone="negative">Stopped</Pill>}
      </div>
      <p className="text-meta text-muted">{scope}</p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-control bg-raised px-2.5 py-1.5 font-mono text-meta text-ink scrollbar-thin">
        {r.when ? `when ${r.when}\n` : ""}{r.check}
      </pre>
      {r.last_dry_run && <DryRun run={r.last_dry_run} />}
      {r.status === "suggested" && can("template.publish") && (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" disabled={add.isPending || dismiss.isPending} onClick={() => add.mutate()}>Add rule</Button>
          <Button asChild variant="secondary" size="sm"><Link href={href(`template/rules/${r.id}`)}>Edit first</Link></Button>
          <Button variant="ghost" size="sm" disabled={add.isPending || dismiss.isPending} onClick={() => dismiss.mutate()}>No thanks</Button>
        </div>
      )}
      {r.status === "suggested" && !can("template.publish") && (
        <p className="text-meta text-muted">Someone who can publish the template adds it.</p>
      )}
      {(add.error || dismiss.error) && <p className="text-body text-negative">{(add.error || dismiss.error)!.message}</p>}
    </div>
  );
}

/** The rule a suggest_rule step created, read from its (possibly cut-off) result. */
export function suggestedRuleId(result?: string): string | undefined {
  return /"id":\s*"([^"]+)"/.exec(result ?? "")?.[1];
}
