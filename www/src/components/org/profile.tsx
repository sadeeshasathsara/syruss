"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { initials } from "@/components/shell/UserMenu";
import { SavedNote, Section, SettingsHeader } from "@/components/settings/kit";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return ["UTC"];
  }
}

function ProfileForm({ me }: { me: Me }) {
  const queryClient = useQueryClient();
  const [first, setFirst] = useState(me.first_name ?? "");
  const [last, setLast] = useState(me.last_name ?? "");
  const [phone, setPhone] = useState(me.phone ?? "");
  const [title, setTitle] = useState(me.job_title ?? "");
  const [timezone, setTimezone] = useState(me.timezone);
  const save = useMutation({
    mutationFn: () => api.me.update({ first_name: first.trim(), last_name: last.trim(), phone: phone.trim(), job_title: title.trim(), timezone }),
    onSuccess: (data) => {
      queryClient.setQueryData(["me"], data);
      queryClient.invalidateQueries({ queryKey: ["members"] });
    },
  });
  const dirty =
    first.trim() !== (me.first_name ?? "") || last.trim() !== (me.last_name ?? "") || phone.trim() !== (me.phone ?? "") ||
    title.trim() !== (me.job_title ?? "") || timezone !== me.timezone;
  const name = `${first} ${last}`.trim() || me.name;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && first.trim() && last.trim()) save.mutate();
      }}
    >
      <Section title="About you" description="How you appear to your team in every workspace.">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <span aria-hidden className="flex size-12 items-center justify-center rounded-full bg-primary text-section font-semibold text-primary-ink">
              {initials(name)}
            </span>
            <span className="text-body text-muted">{me.email}</span>
          </div>
          <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-2">
            <Field label="First name">{(id) => <Input id={id} value={first} onChange={(e) => setFirst(e.target.value)} required maxLength={100} autoComplete="given-name" />}</Field>
            <Field label="Last name">{(id) => <Input id={id} value={last} onChange={(e) => setLast(e.target.value)} required maxLength={100} autoComplete="family-name" />}</Field>
          </div>
          <Field label="Email" help="This is your sign-in, so it can't be changed here.">
            {(id) => <Input id={id} type="email" value={me.email ?? ""} disabled readOnly />}
          </Field>
          <Field label="Phone (optional)">{(id) => <Input id={id} type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={32} autoComplete="tel" />}</Field>
          <Field label="Job title (optional)">{(id) => <Input id={id} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255} />}</Field>
        </div>
      </Section>
      <Section title="Time zone" description="Times in notifications and activity are shown in it.">
        <Select label="Time zone" value={timezone} onChange={setTimezone} options={timeZones().map((z) => ({ value: z, label: z.replaceAll("_", " ") }))} />
      </Section>
      <div className="flex items-center gap-3 pt-2">
        <Button type="submit" variant="primary" disabled={!dirty || !first.trim() || !last.trim() || save.isPending}>Save profile</Button>
        <SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess && !dirty} />
      </div>
    </form>
  );
}

export function ProfileDetails() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me.get });
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Your profile" description="Your details across every workspace in this organization." />
      {me.data ? <ProfileForm key={me.data.user_id} me={me.data} /> : <div className="flex flex-col gap-3 pt-6">{[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>}
    </div>
  );
}
