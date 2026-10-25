"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, BookUser, Cable, ClipboardCheck, FileText, Gauge, LayoutTemplate, MessagesSquare, ScrollText, Settings, Table2 } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { AgentThread, ChatTabs } from "@/components/chat/AgentThread";
import { ChatProvider } from "@/components/chat/ChatProvider";
import { AppFrame } from "@/components/shell/AppFrame";
import { Brand } from "@/components/shell/Brand";
import { NavList, OrgSwitcher, WorkspaceSwitcher, type NavItem } from "@/components/shell/Nav";
import { Loader } from "@/components/shell/Loader";
import { OrgFrame } from "@/components/shell/OrgFrame";
import { SETTINGS, settingsHref } from "@/components/settings/sections";
import { Bar, ErrorNote } from "@/components/ui/Blocks";
import { ApiError, api } from "@/lib/api";
import { useAccess } from "@/lib/access";
import { LiveProvider } from "@/lib/live";
import type { Permission, ScreenSummary, Workspace } from "@/lib/types";
import { WorkspaceProvider } from "@/lib/workspace";

/** Pages a draft workspace can open; everything else waits for a published template. */
const DRAFT_PAGES = ["setup", "settings"];

/** What each part of a workspace needs: a role that doesn't grant it never sees the page, only an explanation. */
const SECTION_NEEDS: Record<string, Permission> = {
  "": "documents.view", documents: "documents.view", review: "documents.view", template: "documents.view",
  setup: "template.publish", knowledge: "knowledge.view", agent: "agent.chat", analytics: "documents.view", connections: "integrations.manage",
};

/** Settings in the workspace's navigation: its sections are the sub tabs. */
function settingsItem(base: string): NavItem {
  return {
    href: settingsHref(base, "general"), label: "Settings", icon: Settings, also: [`${base}/settings`],
    children: SETTINGS.map((s) => ({ href: settingsHref(base, s.key), label: s.label })),
  };
}

/** Analytics: "Your screens" is the overview, each saved analytics screen is a sub tab, and what Syruss suggested waits under "Suggested". */
function screensItem(base: string, screens: ScreenSummary[]): NavItem {
  const active = screens.filter((s) => s.status === "active");
  const suggested = screens.filter((s) => s.status === "suggested");
  const children = [
    { href: `${base}/analytics`, label: "Your screens" },
    ...active.map((s) => ({ href: `${base}/analytics/${s.slug}`, label: s.name })),
    ...(suggested.length ? [{ href: `${base}/analytics/suggested`, label: "Suggested", count: suggested.length }] : []),
  ];
  return {
    href: `${base}/analytics`, label: "Analytics", icon: Table2, badge: "Beta", count: suggested.length || undefined,
    children,
  };
}

/** Connections: how documents come in from other systems, where approved data goes out, and the developers' guide. */
function connectionsItem(base: string, failing: number): NavItem {
  const c = `${base}/connections`;
  return {
    href: c, label: "Connections", icon: Cable, count: failing || undefined,
    children: [
      { href: c, label: "Overview" },
      { href: `${c}/receive`, label: "Receive" },
      { href: `${c}/publish`, label: "Publish" },
      { href: `${c}/deliveries`, label: "Deliveries", count: failing || undefined },
      { href: `${c}/developers`, label: "Developers" },
    ],
  };
}

function navFor(org: string, w: Workspace, sees: (p?: Permission) => boolean, screens: ScreenSummary[], failing: number): NavItem[] {
  const base = `/${org}/w/${w.slug}`;
  if (w.status === "draft") {
    return [...(sees("template.publish") ? [{ href: `${base}/setup`, label: "Design the template", icon: LayoutTemplate }] : []), settingsItem(base)];
  }
  const docs = `${base}/documents`;
  const entries: [Permission | undefined, NavItem][] = [
    ["documents.view", { href: base, label: "Overview", icon: Gauge, exact: true }],
    ["documents.view", { href: docs, label: "Documents", icon: FileText, children: [
      { href: docs, label: "All documents" },
      { href: `${docs}?view=working`, label: "Being read" },
      { href: `${docs}?view=review`, label: "Needs review", count: w.needs_review },
      { href: `${docs}?view=waiting`, label: "Waiting for an answer" },
      { href: `${docs}?view=failed`, label: "Failed" },
      { href: `${docs}?view=done`, label: "Done" },
    ] }],
    ["documents.view", { href: `${base}/review`, label: "Review", icon: ClipboardCheck, count: w.needs_review + w.open_questions, children: [
      { href: `${base}/review`, label: "To review", count: w.needs_review + w.open_questions },
      { href: `${base}/review?tab=history`, label: "Reviewed" },
    ] }],
    ["documents.view", screensItem(base, screens)],
    ["integrations.manage", connectionsItem(base, failing)],
    ["agent.chat", { href: `${base}/agent`, label: "Agent inbox", icon: MessagesSquare, count: (w.open_conversations ?? 0) + w.pending_proposals, children: [
      { href: `${base}/agent`, label: "Waiting on you", count: (w.open_conversations ?? 0) + w.pending_proposals },
      { href: `${base}/agent?tab=history`, label: "Decided" },
    ] }],
    ["knowledge.view", { href: `${base}/knowledge`, label: "Knowledge", icon: BookUser, children: [
      { href: `${base}/knowledge`, label: "People and things" },
      { href: `${base}/knowledge?tab=sources`, label: "Data sources" },
      { href: `${base}/knowledge?tab=memory`, label: "What Syruss remembers" },
    ] }],
    ["documents.view", { href: `${base}/template`, label: "Template", icon: LayoutTemplate, also: [`${base}/setup`], children: [
      { href: `${base}/template`, label: "Current version" },
      { href: `${base}/template?tab=versions`, label: "Version history" },
      { href: `${base}/template?tab=quality`, label: "Quality" },
      ...(sees("template.publish") ? [{ href: `${base}/setup`, label: "Refine with samples" }] : []),
    ] }],
    [undefined, { href: `${base}/log`, label: "Extraction log", icon: ScrollText }],
    [undefined, settingsItem(base)],
  ];
  return entries.filter(([need]) => sees(need)).map(([, item]) => item);
}

/** Inside settings, its sections are the main tabs and their parts the sub tabs. */
function settingsNav(base: string, sees: (p?: Permission) => boolean): NavItem[] {
  return SETTINGS.filter((s) => sees(s.needs)).map((s) => ({
    href: settingsHref(base, s.key), label: s.label, icon: s.icon, also: [`${base}/settings/${s.key}`],
    children: s.subs.map((sub) => ({ href: settingsHref(base, s.key, sub.key), label: sub.label })),
  }));
}

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const { org, ws } = useParams<{ org: string; ws: string }>();
  const pathname = usePathname();
  const router = useRouter();
  // The second gate: asked again every time you enter, and every call after it is checked by the API too.
  const workspace = useQuery({
    queryKey: ["workspace", ws],
    queryFn: () => api.workspaces.get(ws),
    refetchInterval: 30_000,
    staleTime: 0,
    refetchOnMount: "always",
    retry: (count, e) => !(e instanceof ApiError && e.status < 500) && count < 2,
  });
  const { sees, ready } = useAccess(workspace.data?.id);
  // The saved screens are sub tabs of Analytics. Asked here, above the early returns, so hooks keep their order.
  const screens = useQuery({
    queryKey: ["screens", workspace.data?.id],
    queryFn: () => api.screens.list(workspace.data!.id),
    enabled: !!workspace.data?.id && workspace.data.status !== "draft",
  });

  // Deliveries that could not be sent, for the badge on Connections: asked only of people who manage connections.
  const connections = useQuery({
    queryKey: ["connections", workspace.data?.id, "overview"],
    queryFn: () => api.connections.overview(workspace.data!.id),
    enabled: !!workspace.data?.id && workspace.data.status !== "draft" && ready && sees("integrations.manage"),
    refetchInterval: 60_000,
  });

  const section = pathname.split(`/w/${ws}`)[1]?.split("/")[1] ?? "";
  const isDraft = workspace.data?.status === "draft";
  const blocked = isDraft && !DRAFT_PAGES.includes(section);

  useEffect(() => {
    if (blocked) router.replace(`/${org}/w/${ws}/setup`);
  }, [blocked, org, ws, router]);

  if (workspace.error instanceof ApiError && workspace.error.status === 404) {
    return (
      <OrgFrame org={org} title="Workspace not found">
        <div className="flex max-w-lg flex-col gap-3">
          <h1 className="text-title font-semibold">No workspace called “{ws}”</h1>
          <p className="text-body text-muted">It may have been renamed or deleted. Pick it from your workspaces.</p>
          <Link href={`/${org}`} className="text-body font-medium underline underline-offset-2">
            All workspaces
          </Link>
        </div>
      </OrgFrame>
    );
  }

  if (workspace.error instanceof ApiError && workspace.error.status === 403) {
    return (
      <OrgFrame org={org} title="No access">
        <div className="flex max-w-lg flex-col gap-3">
          <h1 className="text-title font-semibold">You’re not a member of this workspace</h1>
          <p className="text-body text-muted">
            Being in the organization isn’t enough: a workspace opens only for the people added to it, each with a role. Ask someone who runs it
            to invite you. Organization admins can join it from the workspace list.
          </p>
          <Link href={`/${org}`} className="text-body font-medium underline underline-offset-2">
            Back to workspaces
          </Link>
        </div>
      </OrgFrame>
    );
  }

  if (!workspace.data) {
    return workspace.isError ? (
      <OrgFrame org={org} title="Workspace">
        <ErrorNote message="Couldn't open this workspace. Check your connection and try again." onRetry={() => workspace.refetch()} />
      </OrgFrame>
    ) : (
      <Loader label="Opening your workspace" />
    );
  }

  const w = workspace.data;
  const base = `/${org}/w/${w.slug}`;
  const inSettings = section === "settings";
  const sidebar = inSettings ? (
    <>
      <Brand href={`/${org}`} />
      <Link
        href={w.status === "draft" ? `${base}/setup` : base}
        className="flex h-8 items-center gap-2 rounded-control px-2.5 text-body text-muted transition-colors hover:bg-active/60 hover:text-ink pointer-coarse:h-11"
      >
        <ArrowLeft className="size-4 flex-none" />
        <span className="min-w-0 truncate">Back to {w.name}</span>
      </Link>
      <p className="px-2.5 pt-1 text-meta font-medium text-muted">Workspace settings</p>
      <NavList label="Workspace settings" items={settingsNav(base, sees)} />
    </>
  ) : (
    <>
      <Brand href={`/${org}`} />
      <OrgSwitcher org={org} />
      <WorkspaceSwitcher org={org} current={w} />
      <NavList label={w.name} items={navFor(org, w, sees, screens.data ?? [], connections.data?.failing ?? 0)} />
    </>
  );

  return (
    <WorkspaceProvider org={org} workspace={w}>
      <LiveProvider workspaceId={w.id}>
        <ChatProvider workspaceId={w.id}>
          <AppFrame
            sidebar={sidebar}
            title={w.name}
            topbar={
              <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-body">
                <Link href={`/${org}/workspaces`} className="text-muted hover:text-ink">
                  Workspaces
                </Link>
                <span className="text-faint">/</span>
                {inSettings ? (
                  <>
                    <Link href={base} className="truncate text-muted hover:text-ink">{w.name}</Link>
                    <span className="text-faint">/</span>
                    <span className="truncate font-medium text-ink">Settings</span>
                  </>
                ) : (
                  <span className="truncate font-medium text-ink">{w.name}</span>
                )}
              </nav>
            }
            chat={inSettings || !sees("agent.chat") ? undefined : <AgentThread />}
            chatTabs={inSettings || !sees("agent.chat") ? undefined : <ChatTabs />}
          >
            {blocked ? null : ready && !sees(SECTION_NEEDS[section]) ? (
              <div className="flex max-w-lg flex-col gap-3">
                <h1 className="text-title font-semibold">Your role doesn’t include this</h1>
                <p className="text-body text-muted">
                  Your role here ({workspace.data.my_role_name ?? "member"}) can’t open this part of {w.name}. Ask an owner or admin if you need it.
                </p>
              </div>
            ) : children}
          </AppFrame>
        </ChatProvider>
      </LiveProvider>
    </WorkspaceProvider>
  );
}
