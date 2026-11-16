import { redirect } from "next/navigation";

/** Accounts opens on its first sub-tab. */
export default function AccountsIndex() {
  redirect("/accounts/organizations");
}
