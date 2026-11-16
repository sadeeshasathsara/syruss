import { OAuthReturn } from "@/components/email/OAuthReturn";

/** Where Microsoft sends you back after you approved (or didn't) Syruss's access to a mailbox. */
export default function MicrosoftReturnPage() {
  return <OAuthReturn provider="microsoft" />;
}
