"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Laptop } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { NoAccess, SavedNote, Section, SettingRow, SettingsHeader } from "@/components/settings/kit";
import { Bar, Empty, ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Switch } from "@/components/ui/Switch";
import { ACCOUNT_URL, useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { applyTheme, useTheme } from "@/lib/browser";
import { ago, cn, dateTime } from "@/lib/format";
import { setSound, useSoundOn } from "@/lib/sound";

// ---- the organization ---------------------------------------------------------------------------

export function OrganizationGeneral() {
  const { org } = useParams<{ org: string }>();
  const { canOrg } = useMe();
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: ["org-overview"], queryFn: api.org.overview });
  const [name, setName] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const current = overview.data?.organization.name ?? "";
  const value = name ?? current;
  const manage = canOrg("org.manage");
  const rename = useMutation({
    mutationFn: () => api.org.rename(value.trim()),
    onSuccess: () => {
      setName(null);
      for (const key of ["org-overview", "organization", "org-activity"]) queryClient.invalidateQueries({ queryKey: [key] });
    },
  });
  const link = typeof window === "undefined" ? "" : `${window.location.origin}/${org}`;

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Organization" description="The name your team sees, and the address your links use." />
      <Section title="Name" description="Shown to everyone in the organization and in invitation emails.">
        {overview.isLoading ? <Bar className="h-9 w-full" /> : (
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (value.trim() && value.trim() !== current) rename.mutate(); }}>
            <Field label="Organization name">{(id) => <Input id={id} value={value} maxLength={120} disabled={!manage} onChange={(e) => setName(e.target.value)} />}</Field>
            <div className="flex items-center gap-3">
              <Button type="submit" variant="primary" disabled={!manage || !value.trim() || value.trim() === current || rename.isPending}>Save name</Button>
              <SavedNote pending={rename.isPending} error={rename.error} saved={rename.isSuccess && name === null} />
            </div>
            {!manage && <NoAccess what="rename the organization" role={undefined} />}
          </form>
        )}
      </Section>
      <Section title="Address" description="It appears in your links and can't be changed here, so existing links keep working.">
        <div className="flex min-w-0 items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink">{link || `/${org}`}</code>
          <Button variant="secondary" onClick={async () => { await navigator.clipboard.writeText(link); setCopied(true); }}>
            {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        {overview.data && <p className="mt-3 text-meta text-muted">Created {dateTime(overview.data.organization.created_at)}.</p>}
      </Section>
    </div>
  );
}

export function CloseOrganization() {
  const { me } = useMe();
  const overview = useQuery({ queryKey: ["org-overview"], queryFn: api.org.overview });
  const [typed, setTyped] = useState("");
  const name = overview.data?.organization.name ?? "";
  const close = useMutation({ mutationFn: () => api.org.close(typed.trim()), onSuccess: () => window.location.assign("/signed-out") });
  const owner = me?.org_role === "owner";

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Close the organization" description="Closing stops the organization opening for everyone. Nothing is erased straight away." />
      <Section title="Close it" description="Only an owner can. You will be asked to sign in again first, and to type the name.">
        {!owner ? <NoAccess what="close the organization" role={me?.org_role ?? undefined} /> : (
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (typed.trim() === name) close.mutate(); }}>
            <Field label={`Type “${name}” to confirm`}>{(id) => <Input id={id} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />}</Field>
            {close.error && <p className="text-body text-negative">{close.error.message}</p>}
            <div><Button type="submit" variant="danger" disabled={typed.trim() !== name || close.isPending}>Close the organization</Button></div>
          </form>
        )}
      </Section>
    </div>
  );
}

// ---- you: sign-in, browsers, history ------------------------------------------------------------

export function SignInSettings() {
  const { me } = useMe();
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Sign-in" description="How you prove it is you. Your password and two-step verification are kept by our sign-in service." />
      <Section title="Your email" description="This is your sign-in and where codes and invitations arrive.">
        <p className="text-body text-ink">{me?.email ?? <Bar className="inline-block h-4 w-48" />}</p>
      </Section>
      <Section title="Password and two-step verification" description="Change your password, or add an authenticator app so a stolen password isn't enough.">
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="secondary"><a href={`${ACCOUNT_URL}/#/security/signingin`}>Change password</a></Button>
          <Button asChild variant="secondary"><a href={`${ACCOUNT_URL}/#/security/signingin`}>Set up two-step verification</a></Button>
        </div>
        <p className="mt-3 text-meta text-muted">You&apos;ll be asked to sign in again first. Risky actions here always ask for a recent sign-in.</p>
      </Section>
    </div>
  );
}

export function TrustedDevices() {
  const queryClient = useQueryClient();
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.me.devices });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["devices"] });
  const remove = useMutation({ mutationFn: (id: string) => api.me.removeDevice(id), onSuccess: refresh });
  const removeOthers = useMutation({ mutationFn: api.me.removeOtherDevices, onSuccess: refresh });
  const others = (devices.data ?? []).filter((d) => !d.current).length;

  return (
    <div className="flex flex-col">
      <SettingsHeader
        title="Trusted browsers"
        description="A browser you confirm with an emailed code is trusted for a while, so you aren't asked every time. A browser we haven't seen is always asked."
        actions={others > 0 ? <Button variant="secondary" disabled={removeOthers.isPending} onClick={() => removeOthers.mutate()}>Remove all other browsers</Button> : undefined}
      />
      <div className="pt-6">
        {devices.isError && <ErrorNote message="Couldn't load your browsers." onRetry={() => devices.refetch()} />}
        {devices.isLoading && <Bar className="h-14 w-full" />}
        {devices.data && devices.data.length === 0 && <Empty title="No trusted browsers yet">Sign in again and this browser is added.</Empty>}
        {devices.data && devices.data.length > 0 && (
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card">
            {devices.data.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <Laptop aria-hidden className="size-5 flex-none text-muted" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-body font-medium text-ink">{d.label}{d.current && <Pill tone="accent">This browser</Pill>}</span>
                  <span className="block truncate text-meta text-muted">
                    Last used {ago(d.last_seen_at).toLowerCase()}{d.ip ? `, from ${d.ip}` : ""}. {d.remember && d.trusted_until ? `Trusted until ${dateTime(d.trusted_until)}.` : "Trusted for this visit only."}
                  </span>
                </span>
                <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate(d.id)}>{d.current ? "Sign this one out" : "Remove"}</Button>
              </li>
            ))}
          </ul>
        )}
        {(remove.error || removeOthers.error) && <p className="mt-2 text-body text-negative">{(remove.error ?? removeOthers.error)!.message}</p>}
      </div>
    </div>
  );
}

export function SecurityActivity() {
  const log = useInfiniteQuery({
    queryKey: ["security-events"],
    queryFn: ({ pageParam }) => api.me.securityEvents(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
  });
  const items = (log.data?.pages ?? []).flatMap((p) => p.items);
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Security activity" description="New browsers, sign-in codes and removals on your account. If something here isn't you, remove the browser and change your password." />
      <div className="pt-6">
        {log.isError && <ErrorNote message="Couldn't load the activity." onRetry={() => log.refetch()} />}
        {log.isLoading && <div className="flex flex-col gap-3">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>}
        {log.data && items.length === 0 && <Empty title="Nothing yet">Security events for your account appear here.</Empty>}
        {items.length > 0 && (
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-card px-4">
            {items.map((e) => (
              <li key={e.id} className="flex items-baseline gap-4 py-3">
                <span className="min-w-0 flex-1 text-body text-ink">{e.summary}{e.ip ? <span className="text-muted"> ({e.ip})</span> : null}</span>
                <time dateTime={e.created_at} className="num flex-none text-meta text-muted">{dateTime(e.created_at)}</time>
              </li>
            ))}
          </ul>
        )}
        {log.hasNextPage && <div className="pt-3"><Button size="sm" variant="secondary" disabled={log.isFetchingNextPage} onClick={() => log.fetchNextPage()}>Load older</Button></div>}
      </div>
    </div>
  );
}

// ---- how the app behaves for you ------------------------------------------------------------------

export function NotificationPreferences() {
  const sound = useSoundOn();
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Notifications" description="How the app tells you about things on this device." />
      <Section title="On this device" description="Saved in this browser only.">
        <SettingRow
          label="Play a sound for new notifications"
          help="A short chime when something new arrives while you have the app open."
          control={<Switch label="Play a sound for new notifications" checked={sound} onChange={setSound} />}
        />
      </Section>
      <Section title="What you're told about" description="Which events notify you is set per workspace, under that workspace's Settings, then Notifications.">
        <p className="text-body text-muted">Invitations and security notices always reach you.</p>
      </Section>
    </div>
  );
}

export function Appearance() {
  const theme = useTheme();
  const choose = applyTheme;
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Appearance" description="How the app looks on this device." />
      <Section title="Theme" description="Saved in this browser only.">
        <div className="flex gap-2" role="radiogroup" aria-label="Theme">
          {(["light", "dark"] as const).map((t) => (
            <button
              key={t}
              type="button"
              role="radio"
              aria-checked={theme === t}
              onClick={() => choose(t)}
              className={cn("h-9 rounded-control border px-4 text-body capitalize transition-colors", theme === t ? "border-ink bg-active font-medium text-ink" : "border-border text-muted hover:text-ink")}
            >
              {t}
            </button>
          ))}
        </div>
      </Section>
    </div>
  );
}
