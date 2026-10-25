import { redirect } from "next/navigation";

// Where to go is decided after sign-in: finish setting up, or into your organization's workspace.
export default function Home() {
  redirect(`${(process.env.SITE_URL ?? "http://localhost:3100").replace(/\/$/, "")}/continue`);
}
