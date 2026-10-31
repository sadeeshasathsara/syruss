import { redirect } from "next/navigation";

// Invitations are accepted on the site, where new people also set up their account.
export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  redirect(`${(process.env.SITE_URL ?? "http://localhost:3100").replace(/\/$/, "")}/invite/${encodeURIComponent(token)}`);
}
