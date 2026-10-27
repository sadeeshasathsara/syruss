"use client";

import { formatRuleCode, formatRuleDescription, RULE_TYPES } from "@/components/workspace/ValidationRulesEditor";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { QualityView } from "@/components/quality/QualityView";
import { Bar, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { dateTime, fieldName, humanize } from "@/lib/format";
import type { TemplateField } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const TYPE_LABEL: Record<string, string> = {
  string: "Text", number: "Number", integer: "Whole number", date: "Date", time: "Time", boolean: "Yes / no",
};

export default function TemplatePage() {
  return useSearchParams().get("tab") === "quality" ? <QualityView /> : <TemplateVersions />;
}

function TemplateVersions() {
  const { workspace, href } = useWorkspace();
  const template = useQuery({ queryKey: ["template", workspace.id], queryFn: () => api.template.get(workspace.id) });
  const versions = useQuery({ queryKey: ["template", workspace.id, "versions"], queryFn: () => api.template.versions(workspace.id) });
  const active = versions.data?.find((v) => v.status === "ACTIVE");
  const schema = template.data?.schema_definition;
  const showHistory = useSearchParams().get("tab") === "versions";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={showHistory ? "Version history" : "Template"}
        subtitle={active ? `Version ${active.version}, published ${dateTime(active.activated_at ?? active.created_at)}. The agent reads every document against it.` : " "}
        actions={
          <Button asChild variant="secondary">
            <Link href={href("setup")}>Refine with new samples</Link>
          </Button>
        }
      />

      {showHistory ? null : !schema ? (
        <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
      ) : (
        <div className="grid grid-cols-1 items-start gap-5 @4xl:grid-cols-2">
          {schema.objects.map((o) => (
            <Panel key={o.type} title={o.type} aside={o.multiple === false ? "One per document" : "Many per document"}>
              {o.description && <p className="mb-3 text-body text-muted">{o.description}</p>}
              <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4">
                {o.fields.map((f) => {
                  const field: TemplateField = typeof f === "string" ? { name: f } : f;
                  const isIdentity = o.identity && [o.identity.id_field, o.identity.label_field].includes(field.name);
                  return (
                    <div key={fieldName(f)} className="contents">
                      <dt className="flex items-center gap-2 border-t border-rule py-2 text-body text-ink">
                        {humanize(field.name)}
                        {field.required && <span className="text-meta text-muted">required</span>}
                        {isIdentity && <Pill tone="accent">identity</Pill>}
                      </dt>
                      <dd className="border-t border-rule py-2 text-right text-body text-muted">{TYPE_LABEL[field.type ?? "string"] ?? field.type}</dd>
                    </div>
                  );
                })}
              </dl>
            </Panel>
          ))}
        </div>
      )}

      {!showHistory && (schema?.validation_rules?.length ?? 0) > 0 && (
        <Panel title="Checks & Validation Rules" aside={`${schema!.validation_rules!.length} active`}>
          <div className="flex flex-col gap-2">
            {schema!.validation_rules!.map((r, i) => {
              const meta = RULE_TYPES.find((t) => t.value === r.rule_type);
              return (
                <div key={r.id ?? i} className="flex flex-col gap-1.5 border-b border-rule py-2.5 last:border-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={meta?.tone ?? "neutral"}>{meta?.label ?? humanize(r.rule_type)}</Pill>
                    <span className="inline-flex items-center rounded-control bg-well px-2 py-0.5 font-mono text-meta font-medium text-ink">
                      {r.object}
                    </span>
                  </div>
                  <p className="text-body text-ink">{formatRuleDescription(r)}</p>
                  <code className="self-start rounded bg-well px-2 py-0.5 font-mono text-meta text-muted">
                    {formatRuleCode(r)}
                  </code>
                </div>
              );
            })}
          </div>
        </Panel>
      )}

      {showHistory && (
      <section>
        <ul className="flex flex-col">
          {(versions.data ?? []).map((v) => (
            <li key={v.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-rule py-2.5 last:border-0">
              <span className="num w-14 text-body font-medium text-ink">{v.version}</span>
              <span className="min-w-0 flex-1 text-body text-ink">{v.change_summary || "No summary"}</span>
              {v.status === "ACTIVE" && <Pill tone="positive">Active</Pill>}
              <span className="text-meta text-muted">{dateTime(v.created_at)}{v.created_by === "agent" ? ", proposed by the agent" : ""}</span>
            </li>
          ))}
        </ul>
      </section>
      )}
    </div>
  );
}
