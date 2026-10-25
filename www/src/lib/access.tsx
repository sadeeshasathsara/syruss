"use client";

import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import type { OrgPermission, Permission } from "./types";

/** The site (landing, onboarding, invitations): where people join and where signed-out people go. */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3100").replace(/\/$/, "");

/** Where people manage their password and two-step verification: Keycloak's own account page. */
export const ACCOUNT_URL = (process.env.NEXT_PUBLIC_ACCOUNT_URL ?? "http://localhost:8080/realms/syruss/account").replace(/\/$/, "");

/** You, and what your role in the organization allows. Roles here run the organization, not a workspace's data. */
export function useMe() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me.get, staleTime: 5 * 60_000 });
  const canOrg = (p: OrgPermission) => !!me.data?.org_permissions?.includes(p);
  return { me: me.data, query: me, canOrg };
}

/**
 * What your role lets you do in one workspace, from the API (the same answer every call is checked against).
 * The screens use it to show only what the role allows; the API still refuses the rest.
 */
export function useAccess(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: ["settings", workspaceId],
    queryFn: () => api.settings.get(workspaceId!),
    enabled: !!workspaceId,
    retry: (count, e) => !(e instanceof ApiError && e.status < 500) && count < 2,
  });
  const permissions = query.data?.access.permissions;
  /** Until the answer arrives nothing is hidden (and nothing is allowed to change). */
  const can = (p: Permission) => !!permissions?.includes(p);
  const sees = (p?: Permission) => !p || !permissions || permissions.includes(p);
  return { access: query.data?.access, can, sees, ready: !!permissions };
}
