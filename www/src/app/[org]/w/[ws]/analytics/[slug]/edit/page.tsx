"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ScreenBuilder } from "@/components/screens/ScreenBuilder";
import { Bar, Empty, ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { useAccess } from "@/lib/access";
import { ApiError, api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

export default function EditScreen() {
  const { slug } = useParams<{ slug: string }>();
  const { workspace, href } = useWorkspace();
  const { can, ready } = useAccess(workspace.id);
  const screen = useQuery({
    queryKey: ["screen", workspace.id, slug],
    queryFn: () => api.screens.get(workspace.id, slug),
    retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
  });
  if (ready && !can("screens.manage")) {
    return (
      <Empty title="Your role can't change analytics screens" action={<Button asChild variant="secondary"><Link href={href(`analytics/${slug}`)}>Back to the screen</Link></Button>}>
        Ask an Editor, Admin or Owner of this workspace.
      </Empty>
    );
  }
  if (screen.error instanceof ApiError && screen.error.status === 404) return <Empty title="This screen isn't here">It may have been deleted.</Empty>;
  if (screen.isError) return <ErrorNote message="Couldn't open this screen." onRetry={() => screen.refetch()} />;
  if (!screen.data) return <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-12 w-full" />)}</div>;
  return <ScreenBuilder key={screen.data.id} initial={screen.data} />;
}
