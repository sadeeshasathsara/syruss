"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { Bar, PageHeader, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";

/** Where Google sends you back after you approved (or didn't) Syruss's access to a mailbox. It finishes connecting it once. */
export default function GoogleReturnPage() {
  const params = useSearchParams();
  const code = params.get("code");
  const state = params.get("state");
  const refused = params.get("error");
  const finish = useMutation({ mutationFn: () => api.mail.googleFinish(code!, state!) });
  const started = useRef(false);
  useEffect(() => {
    if (code && state && !started.current) {
      started.current = true;
      finish.mutate();
    }
  }, [code, state, finish]);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Email" subtitle="Connecting a Google account." />
      <Panel>
        {refused ? (
          <p className="text-body text-ink">Google didn&apos;t give access{refused === "access_denied" ? " (it was declined)" : ""}. Nothing was connected.</p>
        ) : !code || !state ? (
          <p className="text-body text-ink">This page finishes a Google sign-in started from the Email page.</p>
        ) : finish.isPending || finish.isIdle ? (
          <Bar className="h-10 w-full" />
        ) : finish.data ? (
          <div className="flex flex-col gap-2">
            <p className="text-body text-ink">Connected <span className="font-medium">{finish.data.address}</span>.</p>
            <div className="flex flex-wrap gap-2">
              {finish.data.granted.map((g) => <Pill key={g} tone="info">{g === "send" ? "Can send" : "Can read"}</Pill>)}
              {finish.data.purposes.map((p) => <Pill key={p} tone="positive">Used for {p}</Pill>)}
            </div>
          </div>
        ) : (
          <Problem error={finish.error} />
        )}
        <p className="mt-4 text-body"><Link href="/email" className="text-ink underline">Back to Email</Link></p>
      </Panel>
    </div>
  );
}
