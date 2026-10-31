import { redirect } from "next/navigation";
import { api, appLogin, requireSession, type Context } from "@/lib/api";
import { safeReturnTo } from "@/lib/auth/config";

export const dynamic = "force-dynamic";

/**
 * Where a signed-in person goes next, always decided here after Keycloak: finish the profile, create an
 * organization, or into the app (back to the page they asked for, when they asked for one).
 */
export default async function Continue({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  const here = next ? `/continue?next=${encodeURIComponent(next)}` : "/continue";
  const session = await requireSession(here);
  const ctx = await api<Context>(session, "/me/context", { returnTo: here });
  const org = ctx.organizations[0];
  if (!ctx.onboarded || !org) redirect("/onboarding");
  const wanted = safeReturnTo(next, "");
  redirect(appLogin(wanted && wanted.startsWith("/") && wanted !== "/" ? wanted : `/${org.slug}`));
}
