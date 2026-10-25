import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 px-4">
      <h1 className="text-title font-semibold text-ink">This page doesn&apos;t exist</h1>
      <p className="text-body text-muted">The address may be mistyped, or the page was moved.</p>
      <Link href="/" className="text-body font-medium text-ink underline underline-offset-2">
        Go to your workspaces
      </Link>
    </main>
  );
}
