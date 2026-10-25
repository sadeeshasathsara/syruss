"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";
import { ShellProvider } from "@/components/shell/ShellContext";
import { Loader } from "@/components/shell/Loader";
import { Button } from "@/components/ui/Button";
import { SITE_URL } from "@/lib/access";
import { ApiError, api, setActiveOrg } from "@/lib/api";
import { NotificationsProvider } from "@/lib/notifications";

function codeOf(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  const d = error.detail as { code?: string } | undefined;
  return d && typeof d === "object" ? d.code : undefined;
}

/**
 * The first gate: an organization you belong to. Every call below names it (X-Org-Slug) and the API checks your
 * membership each time; this layout only explains what a refusal means.
 */
export default function OrgLayout({ children }: { children: React.ReactNode }) {
  const { org } = useParams<{ org: string }>();
  setActiveOrg(org); // before any child asks for data
  useEffect(() => () => setActiveOrg(undefined), []);
  const organization = useQuery({
    queryKey: ["organization", org],
    queryFn: api.organization,
    staleTime: 0,
    refetchOnMount: "always",
    retry: (count, e) => !(e instanceof ApiError && e.status < 500) && count < 2,
  });

  const code = codeOf(organization.error);
  useEffect(() => {
    if (code === "onboarding_required") window.location.assign(`${SITE_URL}/onboarding`);
  }, [code]);

  if (organization.error instanceof ApiError && organization.error.status === 403) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 px-4">
        <h1 className="text-title font-semibold">You can’t open “{org}”</h1>
        <p className="text-body text-muted">
          You’re not a member of this organization, or it doesn’t exist. Ask someone who runs it for an invitation, or go to your own.
        </p>
        <a href={`${SITE_URL}/continue`} className="text-body font-medium underline underline-offset-2">Go to my organization</a>
      </main>
    );
  }
  if (organization.data && organization.data.slug !== org) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 px-4">
        <h1 className="text-title font-semibold">No organization called “{org}”</h1>
        <Link href={`/${organization.data.slug}`} className="text-body font-medium underline underline-offset-2">
          Open {organization.data.name}
        </Link>
      </main>
    );
  }
  if (organization.isError && !organization.data) {
    const status = organization.error instanceof ApiError ? organization.error.status : 0;
    return (
      <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 px-4">
        <h1 className="text-title font-semibold">{status === 0 || status >= 500 ? "We couldn’t reach Syruss" : "Something went wrong"}</h1>
        <p className="text-body text-muted">
          {status === 0 || status >= 500
            ? "The server isn’t answering right now. Nothing is lost; try again in a moment."
            : organization.error instanceof Error ? organization.error.message : "Please try again."}
        </p>
        <div>
          <Button variant="primary" onClick={() => organization.refetch()}>Try again</Button>
        </div>
      </main>
    );
  }
  if (!organization.data) return <Loader label="Checking your access" />;
  return (
    <ShellProvider>
      <NotificationsProvider>{children}</NotificationsProvider>
    </ShellProvider>
  );
}
