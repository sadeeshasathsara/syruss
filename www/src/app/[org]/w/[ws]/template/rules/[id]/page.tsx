"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { Bar, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { RuleEditor } from "@/components/rules/RuleEditor";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

export default function EditRulePage() {
  const { id } = useParams<{ id: string }>();
  const { workspace, href } = useWorkspace();
  const rule = useQuery({ queryKey: ["rules", workspace.id, "one", id], queryFn: () => api.rules.get(workspace.id, id) });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={rule.data ? rule.data.name : "Rule"}
        subtitle={rule.data?.status === "broken" ? `Stopped: ${rule.data.broken_reason ?? "it no longer fits the template"}` :
          rule.data?.status === "suggested" ? "Suggested by Syruss. Saving your changes keeps it a suggestion; add it from the list." : undefined}
        back={{ href: href("template/rules"), label: "Rules" }}
      />
      {rule.isError && <ErrorNote message="Couldn't load the rule." onRetry={() => rule.refetch()} />}
      {rule.isLoading && <Bar className="h-40 w-full" />}
      {rule.data && <RuleEditor key={rule.data.id} initial={rule.data} />}
    </div>
  );
}
