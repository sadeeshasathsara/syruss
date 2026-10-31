import { redirect } from "next/navigation";

/** Your profile lives under settings now; old links and bookmarks land there. */
export default async function ProfileMoved({ params }: { params: Promise<{ org: string }> }) {
  const { org } = await params;
  redirect(`/${org}/settings/profile/details`);
}
