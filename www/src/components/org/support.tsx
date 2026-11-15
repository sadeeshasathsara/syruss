"use client";

import { Section, SettingsHeader } from "@/components/settings/kit";

/**
 * What the Syruss team can see of this organization, said plainly. Staff see documents and the agent's work on them, to
 * keep the service running and to help when asked; there is no switch for it.
 */
export function SupportAccessSettings() {
  return (
    <div className="flex flex-col">
      <SettingsHeader title="Support and privacy"
        description="What the Syruss team can see of this organization, and why." />
      <Section title="What the Syruss team sees"
        description="To keep the service running and to help when you ask, people on the Syruss team can see:">
        <ul className="flex list-disc flex-col gap-1.5 pl-5 text-body text-ink">
          <li>the documents your workspaces receive, and the records read from them;</li>
          <li>what Syruss&apos;s agent did with each document: what it was asked, what it answered, and the steps it took;</li>
          <li>how each document went: when it arrived, its reviews and questions, and where its record was delivered.</li>
        </ul>
      </Section>
      <Section title="What they don't do"
        description="The team doesn't change your documents, records, settings or workspaces for you unless you ask them to, and doesn't use your documents for anything other than running and supporting Syruss.">
        <p className="text-body text-muted">
          Everything the team changes is shown in the organization&apos;s activity. For anything else about your data, ask the Syruss team under Help &amp; support.
        </p>
      </Section>
    </div>
  );
}
