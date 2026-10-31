"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { PageHeader } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { OrgFrame } from "@/components/shell/OrgFrame";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";

export default function NewWorkspace() {
  const { org } = useParams<{ org: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const { query: meQuery, canOrg } = useMe();

  const create = useMutation({
    mutationFn: () => api.workspaces.create(name.trim(), description.trim() || undefined),
    onSuccess: (ws) => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      router.push(`/${org}/w/${ws.slug}/setup`);
    },
  });

  if (meQuery.data && !canOrg("workspaces.create")) {
    return (
      <OrgFrame org={org} title="New workspace">
        <div className="flex max-w-xl flex-col gap-3">
          <h1 className="text-title font-semibold">Your organization role can’t create workspaces</h1>
          <p className="text-body text-muted">Ask an owner or admin of the organization to create one, or to invite you to an existing workspace.</p>
        </div>
      </OrgFrame>
    );
  }

  return (
    <OrgFrame org={org} title="New workspace">
      <div className="flex max-w-xl flex-col gap-6">
        <PageHeader
          title="New workspace"
          subtitle="One workspace per kind of document. Next you'll upload a few samples and design its template with the agent."
          back={{ href: `/${org}/workspaces`, label: "Workspaces" }}
        />
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <Field label="Name" help="What your team calls these documents, for example “Supplier invoices” or “Daily site rosters”.">
            {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} autoFocus maxLength={120} required />}
          </Field>
          <Field label="What it's for (optional)" help="A sentence for your team, and context for the agent.">
            {(id) => <Textarea id={id} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />}
          </Field>
          {create.error && <p className="text-body text-negative">{create.error.message}</p>}
          <div>
            <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>
              {create.isPending ? "Creating…" : "Create and add samples"}
            </Button>
          </div>
        </form>
      </div>
    </OrgFrame>
  );
}
