import type { Metadata } from "next";
import Link from "next/link";
import { AccountShell } from "@/components/AccountShell";
import { ClearSession } from "@/components/ClearSession";

export const metadata: Metadata = { title: "Signed out" };

export default function SignedOut() {
  return (
    <AccountShell signedIn={false}>
      <ClearSession />
      <div className="sheet">
        <h1>You're signed out</h1>
        <p className="lede">Your session has ended on this device.</p>
        <a className="button" href="/api/auth/login?mode=signin&returnTo=/continue">Sign in again</a>
        <p className="lede" style={{ marginTop: 20 }}><Link href="/" style={{ textDecoration: "underline", textUnderlineOffset: 4 }}>Back to the home page</Link></p>
      </div>
    </AccountShell>
  );
}
