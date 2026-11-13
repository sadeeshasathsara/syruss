import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AccountShell } from "@/components/AccountShell";
import { Unavailable } from "@/components/Unavailable";
import { unreachable } from "@/lib/api";
import { safeReturnTo } from "@/lib/auth/config";
import { confirmDevice, deviceStatus, sendDeviceCode, type DeviceStatus } from "./actions";
import { CodeForm } from "@/components/CodeForm";

export const metadata: Metadata = { title: "Confirm this browser" };
export const dynamic = "force-dynamic";

/**
 * A browser the account hasn't used before: a code goes to the account's email, and only then does anything open.
 * Nobody is asked again on a browser they chose to remember.
 */
export default async function VerifyDevice({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const next = safeReturnTo((await searchParams).next, "/continue");
  let status: DeviceStatus;
  try {
    status = await deviceStatus(next);
  } catch (e) {
    if (unreachable(e)) return <Unavailable again={`/verify-device?next=${encodeURIComponent(next)}`} />;
    throw e;
  }
  if (status.status === "trusted") redirect(next);
  // This browser never reached the API (no token yet): signing in again registers it.
  if (status.status === "unknown") redirect(`/api/auth/login?returnTo=${encodeURIComponent(next)}`);

  return (
    <AccountShell>
      <div className="sheet">
        <h1>Confirm it&apos;s you</h1>
        <p className="lede">
          You signed in from <b>{status.device}</b>, a browser we haven&apos;t seen on your account. We emailed a 6-digit code to <b>{status.email}</b>.
        </p>
        <CodeForm
          confirm={confirmDevice}
          send={sendDeviceCode}
          start={status}
          sentTo={status.email}
          hidden={{ next }}
          submitLabel="Continue"
          remember={`Trust this browser for ${status.trust_days} days`}
        />
      </div>
    </AccountShell>
  );
}
