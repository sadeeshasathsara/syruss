"use server";

import { redirect } from "next/navigation";
import { ApiError, api, appLogin, requireSession } from "@/lib/api";

export type AcceptState = { error?: string };

/** Accepts a workspace or organization invitation as the signed-in person, then goes to the app. */
export async function accept(token: string, kind: "workspace" | "organization", _: AcceptState): Promise<AcceptState> {
  const here = `/invite/${token}`;
  const session = await requireSession(here);
  let target: string;
  try {
    if (kind === "workspace") {
      const r = await api<{ organization: { slug: string }; workspace: { slug: string } }>(
        session, `/invitations/${encodeURIComponent(token)}/accept`, { method: "POST", body: {}, returnTo: here });
      target = appLogin(`/${r.organization.slug}/w/${r.workspace.slug}`);
    } else {
      const r = await api<{ organization: { slug: string } }>(
        session, `/org-invitations/${encodeURIComponent(token)}/accept`, { method: "POST", body: {}, returnTo: here });
      target = appLogin(`/${r.organization.slug}`);
    }
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.code === "onboarding_required") redirect(`/onboarding?invite=${encodeURIComponent(token)}`);
      return { error: e.message };
    }
    throw e;
  }
  redirect(target);
}
