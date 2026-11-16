import { OAuthReturn } from "@/components/email/OAuthReturn";

/** Where Google sends you back after you approved (or didn't) Syruss's access to a mailbox. */
export default function GoogleReturnPage() {
  return <OAuthReturn provider="google" />;
}
