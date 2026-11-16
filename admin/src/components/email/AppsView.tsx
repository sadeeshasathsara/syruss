"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { field } from "@/components/ops/Bits";
import { Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import { canSeal, sealForMail } from "@/lib/mailSeal";
import type { MailOverview, MailProvider } from "@/lib/types";
import { PROVIDER_LABEL } from "./OAuthReturn";
import { usePreservedState } from "@/lib/resume";

const GUIDE: Record<MailProvider, string> = {
  google: "Create a Web application client in Google Cloud (Credentials), turn on the Gmail API, and add the address below as its redirect.",
  microsoft: "Register an app in Microsoft Entra, add the address below as a Web redirect, allow Mail.Send and Mail.Read, and create a client secret.",
};

/** The Google and Microsoft apps mailboxes are connected through: each one's client id, secret (sealed here) and redirect address. */
export function AppsView({ o }: { o: MailOverview }) {
  return (
    <>
      {(["google", "microsoft"] as MailProvider[]).map((p) => <ProviderApp key={`${p}:${o.providers[p].tenant ?? ""}`} provider={p} o={o} />)}
    </>
  );
}

function ProviderApp({ provider, o }: { provider: MailProvider; o: MailOverview }) {
  const queryClient = useQueryClient();
  const info = o.providers[provider];
  const label = PROVIDER_LABEL[provider];
  const [clientId, setClientId] = useState("");
  const [tenant, setTenant] = usePreservedState("apps.tenant", info.tenant ?? "");
  const [secret, setSecret] = useState("");
  const [sealing, setSealing] = useState<boolean | null>(null);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    canSeal().then(setSealing);
  }, []);
  const publicKey = o.key.public_key;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["mail"] });
  const save = useMutation({
    mutationFn: async () => {
      const body: { client_id: string; tenant?: string; sealed?: string; client_secret?: string } = { client_id: clientId.trim(), tenant: tenant.trim() };
      if (secret) {
        // The secret is sealed here, to the mail service's key: this app's server and Kafka only ever carry the sealed value.
        if (sealing && publicKey) body.sealed = await sealForMail(publicKey, { client_secret: secret });
        else body.client_secret = secret;
      }
      return api.mail.setApp(provider, body);
    },
    onSuccess: () => { setSecret(""); setClientId(""); refresh(); },
  });
  const clear = useMutation({ mutationFn: () => api.mail.clearApp(provider), onSuccess: () => { setConfirming(false); refresh(); } });
  return (
    <Panel title={`${label} app`} aside={<Pill tone={info.configured ? "positive" : "warning"}>{info.configured ? "Set" : "Not set"}</Pill>}>
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="text-meta text-muted">{GUIDE[provider]}</p>
        <div className="flex flex-col gap-1 text-meta text-muted">
          Redirect address
          <code className="w-fit select-all rounded bg-well px-2 py-1 text-body text-ink">{info.redirect_uri}</code>
        </div>
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-meta text-muted">Client id
            <input value={clientId} autoComplete="off" className={cn(field, "w-96 max-w-full")}
              placeholder={info.configured ? "••••••••••••••••" : ""} onChange={(e) => setClientId(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-meta text-muted">Client secret
            <input type="password" value={secret} autoComplete="off" className={cn(field, "w-72")}
              placeholder={info.configured ? "••••••••••••••••" : ""} onChange={(e) => setSecret(e.target.value)} />
          </label>
          {provider === "microsoft" && (
            <label className="flex flex-col gap-1 text-meta text-muted">Tenant (optional)
              <input value={tenant} className={cn(field, "w-56")} placeholder="common" onChange={(e) => setTenant(e.target.value)} />
            </label>
          )}
        </div>
        {info.configured && <p className="text-meta text-muted">Already set and hidden. Type a new value to replace it{info.from_environment ? " (it was set up on the server)" : ""}.</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" size="sm" variant="primary" disabled={(!info.configured && (!clientId.trim() || !secret)) || (info.configured && !clientId.trim() && !secret && tenant.trim() === (info.tenant ?? "")) || save.isPending}>{save.isPending ? "Saving…" : "Save app"}</Button>
          {info.configured && !info.from_environment && (confirming ? (
            <span className="flex items-center gap-2 text-meta text-muted">Remove it? Mailboxes already connected stop working until it is set again.
              <Button size="sm" variant="danger" disabled={clear.isPending} onClick={() => clear.mutate()}>Yes, remove</Button>
              <Button size="sm" onClick={() => setConfirming(false)}>No</Button>
            </span>
          ) : <Button size="sm" onClick={() => setConfirming(true)}>Remove app</Button>)}
        </div>
        <Problem error={save.error ?? clear.error} />
      </form>
    </Panel>
  );
}
