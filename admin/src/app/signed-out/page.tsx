import { redirect } from "next/navigation";

export const metadata = { title: "Signed out" };

export default function SignedOut() {
  redirect(`${(process.env.SITE_URL ?? "http://localhost:3100").replace(/\/$/, "")}/signed-out`);
}
