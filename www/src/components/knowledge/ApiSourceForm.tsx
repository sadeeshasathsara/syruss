"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useShell } from "@/components/shell/ShellContext";
import { Panel } from "@/components/ui/Blocks";
import { Button, IconButton } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { ApiAuthType, ApiSourceInput, KnowledgeSource } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const AUTH: Record<ApiAuthType, string> = {
  none: "No authentication",
  bearer: "Bearer token",
  basic: "Username and password",
  api_key: "API key",
  oauth2_client_credentials: "OAuth 2.0 client credentials",
};

type Pair = { key: string; value: string };
const toPairs = (o: Record<string, string>): Pair[] => Object.entries(o).map(([key, value]) => ({ key, value }));
const fromPairs = (rows: Pair[]) => Object.fromEntries(rows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.value]));

/** Name/value rows, for query parameters and headers. */
function Pairs({ label, help, rows, onChange, secret }: {
  label: string;
  help: string;
  rows: Pair[];
  onChange: (rows: Pair[]) => void;
  secret?: (key: string) => boolean;
}) {
  const set = (i: number, next: Pair | null) => onChange(next ? rows.map((r, j) => (j === i ? next : r)) : rows.filter((_, j) => j !== i));
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-body font-medium text-ink">{label}</legend>
      <p className="text-meta text-muted">{help}</p>
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_32px] items-center gap-2">
          <Input aria-label={`${label} name`} value={r.key} onChange={(e) => set(i, { ...r, key: e.target.value })} placeholder="Name" />
          <Input
            aria-label={`${label} value`}
            type={secret?.(r.key) ? "password" : "text"}
            value={r.value}
            onChange={(e) => set(i, { ...r, value: e.target.value })}
            placeholder={secret?.(r.key) && !r.value ? "Saved, leave blank to keep" : "Value"}
          />
          <IconButton aria-label={`Remove ${r.key || "row"}`} onClick={() => set(i, null)}>
            <Trash2 />
          </IconButton>
        </div>
      ))}
      <div>
        <Button size="sm" variant="ghost" onClick={() => onChange([...rows, { key: "", value: "" }])}>
          <Plus /> Add
        </Button>
      </div>
    </fieldset>
  );
}

/**
 * Connect an API the server calls on the workspace's behalf. Test it first to see the records
 * it returns; saving hands it to the agent to map. Secrets are write-only: once saved they are
 * never shown again, and a blank secret field keeps the saved value.
 */
export function ApiSourceForm({ existing, onDone }: { existing?: KnowledgeSource; onDone: () => void }) {
  const { workspace } = useWorkspace();
  const { announce } = useShell();
  const queryClient = useQueryClient();
  const saved = existing?.api;
  const savedSecrets = new Set(saved?.auth.saved_secrets ?? []);

  const [name, setName] = useState(existing?.name ?? "");
  const [url, setUrl] = useState(saved?.url ?? "");
  const [method, setMethod] = useState<"GET" | "POST">(saved?.method ?? "GET");
  const [body, setBody] = useState(saved?.body ?? "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [query, setQuery] = useState<Pair[]>(toPairs(saved?.query ?? {}));
  const [headers, setHeaders] = useState<Pair[]>((saved?.header_names ?? []).map((key) => ({ key, value: "" })));
  const [auth, setAuth] = useState<ApiSourceInput["auth"]>({
    type: saved?.auth.type ?? "none",
    username: saved?.auth.username ?? "",
    key_name: saved?.auth.key_name ?? "",
    key_in: saved?.auth.key_in ?? "header",
    token_url: saved?.auth.token_url ?? "",
    client_id: saved?.auth.client_id ?? "",
    scope: saved?.auth.scope ?? "",
  });
  const setA = (patch: Partial<ApiSourceInput["auth"]>) => setAuth((a) => ({ ...a, ...patch }));

  const input = (): ApiSourceInput => ({
    name: name.trim(), url: url.trim(), method, query: fromPairs(query), headers: fromPairs(headers),
    body: method === "POST" ? body : null, notes, auth,
  });

  const test = useMutation({ mutationFn: () => api.sources.testApi(workspace.id, input(), existing?.id) });
  const save = useMutation({
    mutationFn: () => (existing ? api.sources.updateApi(workspace.id, existing.id, input()) : api.sources.addApi(workspace.id, input())),
    onSuccess: (source) => {
      if (!existing) announce(`You connected the API '${source.name}'. The agent is working out how it maps.`, { openChat: true });
      queryClient.invalidateQueries({ queryKey: ["sources", workspace.id] });
      onDone();
    },
  });

  const secret = (field: string, label: string, help?: string) => (
    <Field label={label} help={help}>
      {(id) => (
        <Input
          id={id}
          type="password"
          autoComplete="off"
          value={(auth as Record<string, string | undefined>)[field] ?? ""}
          onChange={(e) => setA({ [field]: e.target.value })}
          placeholder={savedSecrets.has(field) ? "Saved, leave blank to keep" : ""}
        />
      )}
    </Field>
  );

  return (
    <Panel title={existing ? `Settings for ${existing.name}` : "Connect an API"}>
      <form
        className="flex flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (url.trim()) save.mutate();
        }}
      >
        <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-[minmax(0,1fr)_120px_minmax(0,2fr)]">
          <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="HR system" />}</Field>
          <Field label="Method">
            {(id) => (
              <Select id={id} value={method} onChange={setMethod} options={[{ value: "GET", label: "GET" }, { value: "POST", label: "POST" }]} />
            )}
          </Field>
          <Field label="URL">
            {(id) => <Input id={id} type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.example.com/v1/staff" />}
          </Field>
        </div>

        <div className="flex flex-col gap-4 rounded-card bg-raised p-4">
          <Field label="Authentication">
            {(id) => (
              <Select
                id={id}
                value={auth.type}
                onChange={(type) => setA({ type })}
                options={(Object.keys(AUTH) as ApiAuthType[]).map((k) => ({ value: k, label: AUTH[k] }))}
                className="max-w-sm"
              />
            )}
          </Field>
          {auth.type === "bearer" && secret("token", "Token")}
          {auth.type === "basic" && (
            <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-2">
              <Field label="Username">{(id) => <Input id={id} value={auth.username ?? ""} onChange={(e) => setA({ username: e.target.value })} autoComplete="off" />}</Field>
              {secret("password", "Password")}
            </div>
          )}
          {auth.type === "api_key" && (
            <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-[minmax(0,1fr)_140px_minmax(0,1fr)]">
              <Field label="Key name">{(id) => <Input id={id} value={auth.key_name ?? ""} onChange={(e) => setA({ key_name: e.target.value })} placeholder="X-API-Key" />}</Field>
              <Field label="Sent in">
                {(id) => (
                  <Select
                    id={id}
                    value={auth.key_in ?? "header"}
                    onChange={(key_in) => setA({ key_in })}
                    options={[{ value: "header", label: "A header" }, { value: "query", label: "The query" }]}
                  />
                )}
              </Field>
              {secret("key_value", "Key")}
            </div>
          )}
          {auth.type === "oauth2_client_credentials" && (
            <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-2">
              <Field label="Token URL">{(id) => <Input id={id} type="url" value={auth.token_url ?? ""} onChange={(e) => setA({ token_url: e.target.value })} />}</Field>
              <Field label="Scope (optional)">{(id) => <Input id={id} value={auth.scope ?? ""} onChange={(e) => setA({ scope: e.target.value })} />}</Field>
              <Field label="Client ID">{(id) => <Input id={id} value={auth.client_id ?? ""} onChange={(e) => setA({ client_id: e.target.value })} autoComplete="off" />}</Field>
              {secret("client_secret", "Client secret")}
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-5 @3xl:grid-cols-2">
          <Pairs label="Query parameters" help="Added to the URL, e.g. status=active." rows={query} onChange={setQuery} />
          <Pairs
            label="Headers"
            help="Stored encrypted, like the credentials."
            rows={headers}
            onChange={setHeaders}
            secret={(key) => (saved?.header_names ?? []).includes(key)}
          />
        </div>

        {method === "POST" && (
          <Field label="Request body (JSON)">
            {(id) => <Textarea id={id} value={body} onChange={(e) => setBody(e.target.value)} className="font-mono text-meta" />}
          </Field>
        )}
        <Field label="Notes for the agent (optional)" help="What this data is and anything the column names don't say.">
          {(id) => <Textarea id={id} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} />}
        </Field>

        {test.data && (
          <div className="animate-rise rounded-card bg-raised px-4 py-3 text-body">
            <p className="font-medium text-ink">Connected. Records found:</p>
            <ul className="mt-1 flex flex-col gap-1 text-muted">
              {test.data.record_sets.map((r) => (
                <li key={r.records_path}>
                  <span className="num text-ink">{r.count}</span> in {r.records_path ? `“${r.records_path}”` : "the response"}, with{" "}
                  {r.columns.slice(0, 8).join(", ")}
                  {r.columns.length > 8 ? ` and ${r.columns.length - 8} more` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
        {(test.error || save.error) && <p className="text-body text-negative">{(save.error ?? test.error)!.message}</p>}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={!url.trim() || save.isPending}>
            {save.isPending ? "Saving…" : existing ? "Save" : "Connect and map"}
          </Button>
          <Button variant="secondary" disabled={!url.trim() || test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? "Testing…" : "Test the connection"}
          </Button>
          <Button variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Panel>
  );
}
