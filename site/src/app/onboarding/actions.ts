"use server";

import { redirect } from "next/navigation";
import { ApiError, api, appLogin, requireSession, type Context } from "@/lib/api";

export type FormState = {
  error?: string;
  fieldErrors?: Record<string, string>;
  values?: Record<string, string>;
};

const text = (data: FormData, name: string) => String(data.get(name) ?? "").trim();

function isInviteToken(token: string) {
  return /^[A-Za-z0-9_-]{16,80}$/.test(token);
}

export type EmailStatus = { verified: boolean; email: string | null; code_sent: boolean; resend_in: number; code_valid_minutes: number };

const here = (invite?: string) => (invite && isInviteToken(invite) ? `/onboarding?invite=${invite}` : "/onboarding");

/** Whether the address is confirmed and whether a code is waiting to be typed. */
export async function emailStatus(): Promise<EmailStatus> {
  const session = await requireSession("/onboarding");
  return api<EmailStatus>(session, "/me/email-verification", { returnTo: "/onboarding" });
}

/** Emails a one-time code to the address this sign-in belongs to. */
export async function sendCode(): Promise<{ status?: EmailStatus; error?: string }> {
  const session = await requireSession("/onboarding");
  try {
    return { status: await api<EmailStatus>(session, "/me/email-verification/send", { body: {}, returnTo: "/onboarding" }) };
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    // Asked again too soon: the code already sent is still good; show when another may be sent.
    if (e.status === 429) return { status: await emailStatus(), error: e.message };
    return { error: e.message };
  }
}

/** Step one: type the code we emailed. Then on to the profile. */
export async function confirmCode(_: FormState, data: FormData): Promise<FormState> {
  const invite = text(data, "invite");
  const session = await requireSession(here(invite));
  const code = text(data, "code").replace(/\D/g, "");
  if (code.length < 6) return { error: "Enter the 6-digit code from the email." };
  try {
    await api<EmailStatus>(session, "/me/email-verification/confirm", { body: { code }, returnTo: here(invite) });
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
  redirect(here(invite));
}

/** The profile (the account itself, email and password or Google, lives in Keycloak). */
export async function saveProfile(_: FormState, data: FormData): Promise<FormState> {
  const invite = text(data, "invite");
  const returnTo = invite ? `/onboarding?invite=${invite}` : "/onboarding";
  const session = await requireSession(returnTo);
  const values = { first_name: text(data, "first_name"), last_name: text(data, "last_name"), phone: text(data, "phone"), timezone: text(data, "timezone") };

  const fieldErrors: Record<string, string> = {};
  if (!values.first_name) fieldErrors.first_name = "Enter your first name.";
  if (!values.last_name) fieldErrors.last_name = "Enter your last name.";
  if (values.phone && !/^\+?[0-9 ()\-.]{6,24}$/.test(values.phone)) fieldErrors.phone = "Use digits, spaces and + ( ) - only.";
  if (Object.keys(fieldErrors).length) return { fieldErrors, values };

  try {
    await api<Context>(session, "/onboarding", {
      body: { first_name: values.first_name, last_name: values.last_name, phone: values.phone || null, timezone: values.timezone || null },
      returnTo,
    });
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message, values };
    throw e;
  }
  redirect(invite && isInviteToken(invite) ? `/invite/${invite}` : "/onboarding");
}

/** Whether an organization address is free (asked while typing). */
export async function checkSlug(slug: string): Promise<{ available: boolean; reason?: string | null; slug: string }> {
  const session = await requireSession("/onboarding");
  try {
    return await api(session, `/organizations/slug-check?slug=${encodeURIComponent(slug)}`, { returnTo: "/onboarding" });
  } catch (e) {
    if (e instanceof ApiError) return { available: false, reason: e.message, slug };
    throw e;
  }
}

/** Step two: the organization, then into the app at its address. */
export async function createOrganization(_: FormState, data: FormData): Promise<FormState> {
  const session = await requireSession("/onboarding");
  const name = text(data, "name");
  const slug = text(data, "slug");
  const values = { name, slug };
  const fieldErrors: Record<string, string> = {};
  if (!name) fieldErrors.name = "Give your organization a name.";
  if (!slug) fieldErrors.slug = "Choose an address.";
  if (Object.keys(fieldErrors).length) return { fieldErrors, values };

  let target: string;
  try {
    const ctx = await api<Context>(session, "/organizations", { body: { name, slug }, returnTo: "/onboarding" });
    const org = ctx.organizations.find((o) => o.slug === slug) ?? ctx.organizations[0];
    target = appLogin(`/${org.slug}`);
  } catch (e) {
    if (e instanceof ApiError) return e.status === 409 || e.status === 422 ? { fieldErrors: { slug: e.message }, values } : { error: e.message, values };
    throw e;
  }
  redirect(target);
}
