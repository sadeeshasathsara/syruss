"use client";

import { useParams, useRouter } from "next/navigation";
import { type ComponentType, useEffect } from "react";
import { ORG_SETTINGS, orgSettingsHref } from "@/components/org/sections";
import { ProfileDetails } from "@/components/org/profile";
import { Appearance, CloseOrganization, NotificationPreferences, OrganizationGeneral, SecurityActivity, SignInSettings, TrustedDevices } from "@/components/org/settings";
import { OrgFrame } from "@/components/shell/OrgFrame";

const PAGES: Record<string, Record<string, ComponentType>> = {
  organization: { general: OrganizationGeneral, danger: CloseOrganization },
  profile: { details: ProfileDetails },
  security: { "sign-in": SignInSettings, devices: TrustedDevices, activity: SecurityActivity },
  preferences: { notifications: NotificationPreferences, appearance: Appearance },
};

/** /{org}/settings/{section}/{page}. Anything incomplete goes to the first page of its section. */
export default function OrganizationSettings() {
  const { org, path } = useParams<{ org: string; path?: string[] }>();
  const router = useRouter();
  const [section, sub] = path ?? [];
  const Page = section && sub ? PAGES[section]?.[sub] : undefined;

  useEffect(() => {
    if (Page) return;
    router.replace(orgSettingsHref(org, (ORG_SETTINGS.find((s) => s.key === section) ?? ORG_SETTINGS[0]).key));
  }, [Page, section, org, router]);

  return (
    <OrgFrame org={org} title="Settings">
      {Page ? <div key={`${section}/${sub}`} className="mx-auto w-full max-w-5xl animate-rise"><Page /></div> : null}
    </OrgFrame>
  );
}
