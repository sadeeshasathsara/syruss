"use client";

import * as Menu from "@radix-ui/react-dropdown-menu";
import { useQuery } from "@tanstack/react-query";
import { LogOut, UserRound } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { api } from "@/lib/api";

export function initials(name: string) {
  return name.split(/[\s(]+/).filter((p) => /\w/.test(p)).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("") || "?";
}

/** Your avatar: hover or click for your profile and signing out. */
export function UserMenu() {
  const { org } = useParams<{ org?: string }>();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me.get, staleTime: 5 * 60_000 });
  const [open, setOpen] = useState(false);
  const closing = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const hover = (next: boolean) => {
    clearTimeout(closing.current);
    if (next) setOpen(true);
    else closing.current = setTimeout(() => setOpen(false), 180);
  };
  // Signing out ends the session on this app's server and at Keycloak, so it is a real form post, not a fetch.
  const signOut = () => {
    const form = document.createElement("form");
    form.method = "post";
    form.action = "/api/auth/logout";
    document.body.appendChild(form);
    form.submit();
  };
  const name = me.data?.name ?? "";

  return (
    <Menu.Root open={open} onOpenChange={setOpen} modal={false}>
      <Menu.Trigger asChild>
        <button
          type="button"
          aria-label={name ? `Account: ${name}` : "Account"}
          onPointerEnter={(e) => e.pointerType === "mouse" && hover(true)}
          onPointerLeave={(e) => e.pointerType === "mouse" && hover(false)}
          className="flex size-8 items-center justify-center rounded-full bg-primary text-meta font-semibold text-primary-ink transition-transform hover:scale-105 pointer-coarse:size-10"
        >
          {name ? initials(name) : <UserRound className="size-4" />}
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={8}
          onPointerEnter={() => hover(true)}
          onPointerLeave={() => hover(false)}
          className="z-50 w-64 animate-fade rounded-card border border-border bg-sheet p-1 shadow-float"
        >
          <div className="px-3 py-2.5">
            <p className="truncate text-body font-medium text-ink">{name || "Signed in"}</p>
            <p className="truncate text-meta text-muted">{me.data?.email ?? "No email on file"}</p>
            {me.data?.org_role && <p className="truncate text-meta text-faint">Organization {me.data.org_role}</p>}
          </div>
          <Menu.Separator className="my-1 h-px bg-border" />
          <Menu.Item asChild>
            <Link
              href={`/${org}/settings/profile/details`}
              className="flex h-8 items-center gap-2 rounded-control px-3 text-body text-ink outline-none data-[highlighted]:bg-raised pointer-coarse:h-10"
            >
              <UserRound className="size-4 text-muted" /> Your profile
            </Link>
          </Menu.Item>
          <Menu.Item
            onSelect={signOut}
            className="flex h-8 cursor-pointer items-center gap-2 rounded-control px-3 text-body text-ink outline-none data-[highlighted]:bg-raised pointer-coarse:h-10"
          >
            <LogOut className="size-4 text-muted" /> Sign out
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
