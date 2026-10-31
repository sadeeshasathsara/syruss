"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import { DeliveriesPage } from "@/components/connections/Deliveries";
import { DevelopersPage } from "@/components/connections/Developers";
import { OverviewPage } from "@/components/connections/Overview";
import { DestinationPage, NewDestinationPage, PublishPage } from "@/components/connections/Publish";
import { ChannelPage, ReceivePage } from "@/components/connections/Receive";
import { useWorkspace } from "@/lib/workspace";

const PAGES = ["receive", "publish", "deliveries", "developers"];

/** /connections, /connections/{receive | receive/{channel} | publish | publish/new | publish/{id} | deliveries | developers}. Anything else goes to the overview. */
export default function ConnectionsRoute() {
  const { path } = useParams<{ path?: string[] }>();
  const { href } = useWorkspace();
  const router = useRouter();
  const [page, item] = path ?? [];
  const overview = !page;
  const known = overview || PAGES.includes(page);

  useEffect(() => {
    if (!known) router.replace(href("connections"));
  }, [known, href, router]);

  if (!known) return null;
  return (
    <div key={`${page ?? ""}/${item ?? ""}`} className="mx-auto w-full max-w-5xl animate-rise">
      {overview && <OverviewPage />}
      {page === "receive" && (item ? <ChannelPage slug={item} /> : <ReceivePage />)}
      {page === "publish" && (item === "new" ? <NewDestinationPage /> : item ? <DestinationPage id={item} /> : <PublishPage />)}
      {page === "deliveries" && <DeliveriesPage />}
      {page === "developers" && <DevelopersPage />}
    </div>
  );
}
