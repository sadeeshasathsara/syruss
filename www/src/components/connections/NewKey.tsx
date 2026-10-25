"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { ShowOnce } from "@/components/ui/ShowOnce";
import { api } from "@/lib/api";
import type { IntakeChannelInfo } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const EXPIRY = [
  { value: "never", label: "Never" },
  { value: "90", label: "In 90 days" },
  { value: "365", label: "In a year" },
] as const;

const MODES = [
  { value: "live", label: "Live: documents are read and published" },
  { value: "test", label: "Test: documents are read, never published" },
] as const;

/** Makes a key for one sending system. The secret is shown once, here, and nowhere again. */
export function NewKeyDialog({ channel, open, onOpenChange }: { channel: IntakeChannelInfo; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"live" | "test">("live");
  const [expiry, setExpiry] = useState<(typeof EXPIRY)[number]["value"]>("365");
  const [rate, setRate] = useState("60");
  const [quota, setQuota] = useState("");
  const [addresses, setAddresses] = useState("");
  const [secret, setSecret] = useState<string | null>(null);

  const reset = () => {
    setName(""); setMode("live"); setExpiry("365"); setRate("60"); setQuota(""); setAddresses(""); setSecret(null);
  };
  const create = useMutation({
    mutationFn: () =>
      api.channels.createKey(workspace.id, channel.id, {
        name: name.trim(), mode, expires_in_days: expiry === "never" ? null : Number(expiry),
        rate_limit_per_minute: Math.max(1, Number(rate) || 60), daily_quota: quota.trim() ? Math.max(1, Number(quota)) : null,
        ip_allowlist: addresses.split(/[\s,]+/).filter(Boolean),
      }),
    onSuccess: (res) => {
      setSecret(res.secret);
      queryClient.invalidateQueries({ queryKey: ["channels", workspace.id] });
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => { onOpenChange(next); if (!next) { reset(); create.reset(); } }}
      title={secret ? "Your new key" : `New key for ${channel.name}`}
      description={secret ? undefined : "Give each system that sends documents its own key, so you can see what each one sent and stop one without touching the others."}
      size="lg"
    >
      {secret ? (
        <ShowOnce label={`The key for ${name.trim()}`} value={secret} onDone={() => onOpenChange(false)} />
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}
        >
          <Field label="Which system will use it?">
            {(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Warehouse scanner" required maxLength={120} autoFocus />}
          </Field>
          <Field label="Mode" help={mode === "test" ? "Use a test key while you build your side. Its documents are marked as tests, don’t count toward your plan and are never published." : undefined}>
            {(id) => <Select id={id} value={mode} onChange={setMode} options={[...MODES]} />}
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field label="Requests a minute">{(id) => <Input id={id} inputMode="numeric" value={rate} onChange={(e) => setRate(e.target.value.replace(/\D/g, ""))} />}</Field>
            <Field label="Documents a day" help="Empty: no daily limit">{(id) => <Input id={id} inputMode="numeric" value={quota} onChange={(e) => setQuota(e.target.value.replace(/\D/g, ""))} placeholder="No limit" />}</Field>
            <Field label="Expires">{(id) => <Select id={id} value={expiry} onChange={setExpiry} options={[...EXPIRY]} />}</Field>
          </div>
          <Field label="Allowed addresses" help="Only requests from these IP addresses or ranges (such as 203.0.113.7 or 203.0.113.0/24) are accepted. Empty: any address.">
            {(id) => <Textarea id={id} value={addresses} onChange={(e) => setAddresses(e.target.value)} placeholder="One per line" rows={2} />}
          </Field>
          {create.error && <p className="text-body text-negative">{create.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>{create.isPending ? "Creating…" : "Create key"}</Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
