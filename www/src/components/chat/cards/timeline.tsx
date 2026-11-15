"use client";

import Link from "next/link";
import type { TimelineItem } from "../timeline";
import { ProposalCard } from "./proposal";
import { QuestionCard } from "./question";
import { ChatRunLine, RunCard } from "./run";

function NoteLine({ text, href }: { text: string; href?: string }) {
  return (
    <p className="text-right text-meta text-muted">
      {href ? (
        <Link href={href} className="underline-offset-2 hover:text-ink hover:underline">
          {text}
        </Link>
      ) : (
        text
      )}
    </p>
  );
}

/** Someone from the Syruss team, in live support. Plain text: a person's words, shown as they wrote them. */
function StaffMessage({ name, text }: { name: string; text: string }) {
  return (
    <div className="max-w-[85%] rounded-card rounded-bl-chip border border-accent/30 bg-[var(--pill-accent-bg)] px-3 py-2">
      <p className="mb-0.5 text-meta font-medium text-ink">{name.split(" ")[0]} <span className="font-normal text-muted">· Syruss team</span></p>
      <p className="whitespace-pre-wrap break-words text-body text-ink">{text}</p>
    </div>
  );
}

export function TimelineCard({ data }: { data: TimelineItem }) {
  switch (data.kind) {
    case "run":
      return data.run.trigger === "human.message" ? <ChatRunLine run={data.run} /> : <RunCard run={data.run} />;
    case "question":
      return <QuestionCard question={data.question} />;
    case "proposal":
      return <ProposalCard proposal={data.proposal} />;
    case "note":
      return <NoteLine text={data.text} href={data.href} />;
    case "staff":
      return <StaffMessage name={data.name} text={data.text} />;
    default:
      return null;
  }
}
