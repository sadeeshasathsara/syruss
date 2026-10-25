import Link from "next/link";
import { Wordmark } from "./Brand";

/** The frame around the account pages: a quiet bar, and the work on a white sheet. */
export function AccountShell({ children, signedIn = true }: { children: React.ReactNode; signedIn?: boolean }) {
  return (
    <div className="account">
      <header className="account-bar">
        <Link href="/" aria-label="Syruss, home"><Wordmark /></Link>
        {signedIn ? (
          <form action="/api/auth/logout" method="post">
            <button type="submit">Sign out</button>
          </form>
        ) : null}
      </header>
      <main className="account-main">{children}</main>
    </div>
  );
}
