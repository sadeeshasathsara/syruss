import { redirect } from "next/navigation";
import { Suspense } from "react";
import { Loader } from "@/components/Loader";
import { Unavailable } from "@/components/Unavailable";
import { api, appLogin, requireSession, unreachable, type Context } from "@/lib/api";
import { safeReturnTo } from "@/lib/auth/config";

export const dynamic = "force-dynamic";

async function Resolve({ next }: { next?: string }) {
  const here = next ? `/continue?next=${encodeURIComponent(next)}` : "/continue";
  const session = await requireSession(here);
  let ctx: Context;
  try {
    ctx = await api<Context>(session, "/me/context", { returnTo: here });
  } catch (e) {
    if (unreachable(e)) return <Unavailable again={here} />;
    throw e;
  }
  const org = ctx.organizations[0];
  if (!ctx.onboarded || !org) redirect("/onboarding");
  const wanted = safeReturnTo(next, "");
  redirect(appLogin(wanted && wanted.startsWith("/") && wanted !== "/" ? wanted : `/${org.slug}`));
}

/**
 * Where a signed-in person goes next, always decided here after Keycloak: finish the profile, create an
 * organization, or into the app (back to the page they asked for, when they asked for one).
 * The loader is part of this page, so it shows while the decision is made.
 */
export default async function Continue({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return (
    <Suspense fallback={<Loader label="Signing you in" />}>
      <Resolve next={next} />
    </Suspense>
  );
}
