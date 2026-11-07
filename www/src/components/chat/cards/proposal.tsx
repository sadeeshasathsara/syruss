"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { MappingSummary } from "@/components/knowledge/MappingSummary";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn, fieldName, humanize } from "@/lib/format";
import type { MappingPreview, Proposal, SourceMapping, TemplateSchema } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

/** Field-level difference between the active template and a proposed one. */
export function schemaDiff(current: TemplateSchema | undefined, next: TemplateSchema | undefined) {
  const index = (s?: TemplateSchema) =>
    new Map((s?.objects ?? []).map((o) => [o.type, new Set(o.fields.map(fieldName))]));
  const a = index(current);
  const b = index(next);
  const changes: { object: string; added: string[]; removed: string[]; isNew: boolean; isGone: boolean }[] = [];
  for (const [type, fields] of b) {
    const before = a.get(type);
    const added = [...fields].filter((f) => !before?.has(f));
    const removed = before ? [...before].filter((f) => !fields.has(f)) : [];
    if (!before || added.length || removed.length) changes.push({ object: type, added, removed, isNew: !before, isGone: false });
  }
  for (const [type, fields] of a) if (!b.has(type)) changes.push({ object: type, added: [], removed: [...fields], isNew: false, isGone: true });
  const rules = (next?.validation_rules?.length ?? 0) - (current?.validation_rules?.length ?? 0);
  return { changes, rules };
}

export function ProposalCard({ proposal }: { proposal: Proposal }) {
  const { workspace, href } = useWorkspace();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const template = useQuery({ queryKey: ["template", workspace.id], queryFn: () => api.template.get(workspace.id) });
  const review = useMutation({
    mutationFn: (action: "APPROVE" | "REJECT") => api.proposals.review(workspace.id, proposal.id, action),
    onSuccess: () => {
      ["proposals", "template", "workspace", "workspaces", "sources", "entities"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
    },
  });
  const decided = review.data ?? (proposal.status !== "PENDING" ? proposal : undefined);
  const isTemplate = proposal.proposal_type === "TEMPLATE_CHANGE";
  const isMapping = proposal.proposal_type === "SOURCE_MAPPING";
  const first = isTemplate && !template.data?.active_version_id;
  const diff = isTemplate ? schemaDiff(first ? undefined : template.data?.schema_definition, proposal.proposed_data.schema_definition) : null;

  return (
    <div className={cn("rounded-card px-3 py-3 transition-colors [box-shadow:var(--shadow-card)]", decided ? "bg-card/60" : "bg-card border border-control")}>
      <Pill tone="accent">
        {first
          ? "Proposed template"
          : isTemplate
            ? "Template change"
            : isMapping
              ? `Mapping for ${String(proposal.proposed_data.source_name ?? "a data source")}`
              : humanize(proposal.proposal_type)}
      </Pill>
      <p className="mt-2 text-body text-ink">{proposal.observation}</p>
      {isMapping && !decided && (
        <div className="mt-2">
          <MappingSummary
            mapping={proposal.proposed_data.mapping as SourceMapping}
            preview={proposal.proposed_data.preview as MappingPreview[] | undefined}
          />
        </div>
      )}
      {diff && diff.changes.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 text-body">
          {diff.changes.map((c) => (
            <li key={c.object} className="text-muted">
              <span className="font-medium text-ink">{c.object}</span>
              {c.isNew ? " (new)" : c.isGone ? " (removed)" : ""}
              {c.added.length > 0 && <>: adds {c.added.join(", ")}</>}
              {c.removed.length > 0 && !c.isGone && <>; removes {c.removed.join(", ")}</>}
            </li>
          ))}
          {diff.rules !== 0 && <li className="text-muted">{diff.rules > 0 ? `${diff.rules} new rule(s)` : `${-diff.rules} rule(s) removed`}</li>}
        </ul>
      )}
      {decided ? (
        <p className="mt-2 text-body text-ink">
          {decided.status === "APPROVED"
            ? isMapping ? "Approved and imported." : "Approved and published."
            : decided.reviewer_notes || "Rejected."}
        </p>
      ) : first ? (
        pathname === href("setup") ? (
          <p className="mt-2 text-body text-muted">Edit it on the left, then publish.</p>
        ) : (
          <div className="mt-3">
            <Button asChild size="sm" variant="primary">
              <Link href={href("setup")}>Review the template</Link>
            </Button>
          </div>
        )
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="primary" disabled={review.isPending} onClick={() => review.mutate("APPROVE")}>
            {isMapping ? "Approve and import" : "Approve and publish"}
          </Button>
          <Button size="sm" variant="secondary" disabled={review.isPending} onClick={() => review.mutate("REJECT")}>
            Reject
          </Button>
          {review.error && <p className="w-full text-meta text-negative">{review.error.message}</p>}
        </div>
      )}
    </div>
  );
}
