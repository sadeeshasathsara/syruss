"use client";

import { useParams, useRouter } from "next/navigation";
import { type ComponentType, useEffect } from "react";
import { AuditSettings } from "@/components/settings/audit";
import {
  AutonomySettings, InstructionsSettings, IntakeSettings, NotificationSettings, ReviewRuleSettings,
} from "@/components/settings/behaviour";
import { BillingSettings, DangerZone, ProfileSettings } from "@/components/settings/general";
import { ApiKeySettings, WebhookSettings } from "@/components/settings/integrations";
import { InvitationSettings, PeopleSettings, RoleSettings } from "@/components/settings/members";
import { RunsSettings } from "@/components/settings/runs";
import { SETTINGS } from "@/components/settings/sections";
import { BudgetSettings, ReflectionSettings } from "@/components/settings/upkeep";
import { NoAccess } from "@/components/settings/kit";
import { useAccess } from "@/lib/access";
import { useWorkspace } from "@/lib/workspace";

const PAGES: Record<string, Record<string, ComponentType>> = {
  general: { profile: ProfileSettings, billing: BillingSettings, danger: DangerZone },
  members: { people: PeopleSettings, invitations: InvitationSettings, roles: RoleSettings },
  agent: { autonomy: AutonomySettings, instructions: InstructionsSettings, budget: BudgetSettings, reflection: ReflectionSettings },
  documents: { intake: IntakeSettings, review: ReviewRuleSettings },
  notifications: { preferences: NotificationSettings },
  integrations: { "api-keys": ApiKeySettings, webhooks: WebhookSettings },
  audit: { activity: AuditSettings, runs: RunsSettings },
};

/** /settings/{section}/{sub}. Anything incomplete goes to the first page of its section. */
export default function SettingsRoute() {
  const { path } = useParams<{ path?: string[] }>();
  const { href, workspace } = useWorkspace();
  const { sees, ready, access } = useAccess(workspace.id);
  const router = useRouter();
  const [section, sub] = path ?? [];
  const Page = section && sub ? PAGES[section]?.[sub] : undefined;

  useEffect(() => {
    if (Page) return;
    const target = SETTINGS.find((s) => s.key === section) ?? SETTINGS[0];
    router.replace(href(`settings/${target.key}/${target.subs[0].key}`));
  }, [Page, section, href, router]);

  const needed = SETTINGS.find((s) => s.key === section)?.needs;
  if (ready && !sees(needed)) {
    return <div className="mx-auto w-full max-w-5xl pt-6"><NoAccess what="open this part of the settings" role={access?.role_label} /></div>;
  }

  return Page ? (
    <div key={`${section}/${sub}`} className="mx-auto w-full max-w-5xl animate-rise">
      <Page />
    </div>
  ) : null;
}
