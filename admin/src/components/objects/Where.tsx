import Link from "next/link";
import type { ProfileWhere } from "@/lib/types";

/** The organization and workspace a profile belongs to, as tags: the organization's opens its page. */
export function WhereTags({ where }: { where: ProfileWhere }) {
  return (
    <>
      <Link href={`/accounts/organizations/${where.organization.id}`}
        className="inline-flex h-5 items-center rounded-chip bg-[var(--pill-info-bg)] px-1.5 text-meta font-medium text-[var(--pill-info-ink)] hover:underline">
        {where.organization.name}
      </Link>
      <Link href={`/objects/${where.workspace.id}`}
        className="inline-flex h-5 items-center rounded-chip bg-[var(--pill-neutral-bg)] px-1.5 text-meta font-medium text-[var(--pill-neutral-ink)] hover:underline">
        {where.workspace.name}
      </Link>
    </>
  );
}

/** A profile path from the API (objects/{type}/things/{id}) as an address in this app. */
export const objectPath = (template: string, path: string) => `/objects/${template}/${path.replace(/^objects\//, "")}`;
