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
    default:
      return null;
  }
}
