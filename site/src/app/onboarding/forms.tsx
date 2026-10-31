"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { CodeForm } from "@/components/CodeForm";
import { checkSlug, confirmCode, createOrganization, saveProfile, sendCode, type EmailStatus, type FormState } from "./actions";

const initial: FormState = {};

function Field({ id, label, hint, error, children }: { id: string; label: string; hint?: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && !error ? <span className="hint">{hint}</span> : null}
      {error ? <span className="error" id={`${id}-error`}>{error}</span> : null}
    </div>
  );
}

/**
 * The time zone starts as the one this device uses; it can be changed, and can be left alone. The list of zones
 * differs between the server's runtime and the browser's, so it is read after mount; the first render (server and
 * client alike) holds only the current value.
 */
function TimezoneField({ initialValue }: { initialValue?: string }) {
  const [zone, setZone] = useState(initialValue ?? "UTC");
  const [zones, setZones] = useState<string[]>([]);
  useEffect(() => {
    try {
      setZones((Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf("timeZone"));
    } catch {
      /* the current value alone is enough */
    }
    if (initialValue) return;
    try {
      setZone(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    } catch {
      /* keep UTC */
    }
  }, [initialValue]);
  const options = zones.includes(zone) ? zones : [zone, ...zones];
  return (
    <Field id="timezone" label="Time zone" hint="Picked from your device. Change it if it's wrong, or leave it.">
      <select id="timezone" name="timezone" value={zone} onChange={(e) => setZone(e.target.value)}>
        {options.map((z) => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
      </select>
    </Field>
  );
}

/** Step one: the one-time code that proves the address is theirs. */
export function VerifyEmailForm({ email, invite, start }: { email: string; invite?: string; start: EmailStatus }) {
  return <CodeForm confirm={confirmCode} send={sendCode} start={start} sentTo={email} hidden={{ invite }} submitLabel="Confirm email" />;
}

export function ProfileForm({ email, invite, photo, defaults }: { email: string; invite?: string; photo?: string | null; defaults: { first: string; last: string } }) {
  const [state, action, pending] = useActionState(saveProfile, initial);
  const v = state.values ?? {};
  const e = state.fieldErrors ?? {};
  return (
    <form action={action} noValidate>
      {invite ? <input type="hidden" name="invite" value={invite} /> : null}
      {photo ? (
        <div className="profile-photo">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={photo} alt="" width={56} height={56} referrerPolicy="no-referrer" />
          <span>From your Google account</span>
        </div>
      ) : null}
      <div className="row2">
        <Field id="first_name" label="First name" error={e.first_name}>
          <input id="first_name" name="first_name" autoComplete="given-name" required defaultValue={v.first_name ?? defaults.first}
            aria-invalid={!!e.first_name} aria-describedby={e.first_name ? "first_name-error" : undefined} />
        </Field>
        <Field id="last_name" label="Last name" error={e.last_name}>
          <input id="last_name" name="last_name" autoComplete="family-name" required defaultValue={v.last_name ?? defaults.last}
            aria-invalid={!!e.last_name} aria-describedby={e.last_name ? "last_name-error" : undefined} />
        </Field>
      </div>
      <Field id="email" label="Email" hint="This is your sign-in. It can't be changed here.">
        <input id="email" type="email" value={email} disabled readOnly />
      </Field>
      <Field id="phone" label="Phone (optional)" error={e.phone}>
        <input id="phone" name="phone" type="tel" autoComplete="tel" inputMode="tel" defaultValue={v.phone ?? ""}
          aria-invalid={!!e.phone} aria-describedby={e.phone ? "phone-error" : undefined} />
      </Field>
      <TimezoneField initialValue={v.timezone} />
      {state.error ? <p className="form-error" role="alert">{state.error}</p> : null}
      <button className="button" type="submit" disabled={pending}>{pending ? "Saving your profile…" : "Continue"}</button>
    </form>
  );
}

const slugify = (value: string) =>
  value.toLowerCase().replace(/['’]/g, "").normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

type Check = { state: "idle" | "checking" | "free" | "taken"; reason?: string | null };

/** The organization: a name (starting as "<your name>'s organization") and the address it will live at. */
export function OrganizationForm({ suggestedName }: { suggestedName: string }) {
  const [state, action, pending] = useActionState(createOrganization, initial);
  const v = state.values ?? {};
  const e = state.fieldErrors ?? {};
  const [name, setName] = useState(v.name ?? suggestedName);
  const [slug, setSlug] = useState(v.slug ?? slugify(suggestedName));
  const [edited, setEdited] = useState(false); // once the address is typed by hand, the name stops rewriting it
  const [check, setCheck] = useState<Check>({ state: "idle" });
  const latest = useRef(0);

  useEffect(() => {
    if (!slug) {
      setCheck({ state: "idle" });
      return;
    }
    const mine = ++latest.current;
    setCheck({ state: "checking" });
    const timer = setTimeout(async () => {
      const result = await checkSlug(slug);
      if (mine === latest.current) setCheck({ state: result.available ? "free" : "taken", reason: result.reason });
    }, 350);
    return () => clearTimeout(timer);
  }, [slug]);

  const address = e.slug ?? (check.state === "taken" ? check.reason ?? "That address is taken." : undefined);
  return (
    <form action={action} noValidate>
      <Field id="name" label="Organization name" hint="Your company or team. You can rename it later." error={e.name}>
        <input id="name" name="name" required maxLength={120} value={name} autoFocus
          onChange={(ev) => {
            setName(ev.target.value);
            if (!edited) setSlug(slugify(ev.target.value));
          }}
          aria-invalid={!!e.name} aria-describedby={e.name ? "name-error" : undefined} />
      </Field>
      <Field
        id="slug"
        label="Address"
        error={address}
        hint={check.state === "free" ? "This address is yours." : check.state === "checking" ? "Checking…" : "Letters, numbers and hyphens. It appears in your links."}
      >
        <div className="slug-input">
          <span aria-hidden="true">syruss/</span>
          <input id="slug" name="slug" required maxLength={48} value={slug} spellCheck={false} autoCapitalize="none" autoComplete="off"
            onChange={(ev) => {
              setEdited(true);
              setSlug(ev.target.value.toLowerCase());
            }}
            aria-invalid={!!address} aria-describedby="slug-hint" />
        </div>
      </Field>
      {state.error ? <p className="form-error" role="alert">{state.error}</p> : null}
      <button className="button" type="submit" disabled={pending || check.state === "taken"}>{pending ? "Creating…" : "Create organization"}</button>
    </form>
  );
}
