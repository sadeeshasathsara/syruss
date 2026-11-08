"use client";

import { useState } from "react";
import { Label, MoneyInput, StaffForm } from "@/components/billing/StaffForm";
import { field } from "@/components/ops/Bits";
import { api } from "@/lib/api";
import { KIND_LABEL } from "@/lib/billing";
import type { CatalogItem, CatalogKind, LimitInfo, Limits } from "@/lib/types";

type Mode = { new: CatalogKind } | { edit: CatalogItem } | { version: CatalogItem };

/**
 * A plan, an add-on or a credit pack: a new draft, a draft being changed, or the next version of a published one. The
 * limits offered are the platform's own (only those it enforces), so nothing can be sold that isn't kept.
 */
export function CatalogEditor({ mode, limits, onDone }: { mode: Mode; limits: LimitInfo[]; onDone: () => void }) {
  const base = "new" in mode ? null : "edit" in mode ? mode.edit : mode.version;
  const kind: CatalogKind = "new" in mode ? mode.new : base!.kind;
  const [key, setKey] = useState(base?.key ?? "");
  const [name, setName] = useState(base?.name ?? "");
  const [description, setDescription] = useState(base?.description ?? "");
  const [cents, setCents] = useState(base?.price_cents ?? 0);
  const [currency, setCurrency] = useState(base?.currency ?? "USD");
  const [isPublic, setPublic] = useState(base?.public ?? true);
  const [pages, setPages] = useState<number | null>(base?.pages ?? (kind === "credits" ? 1000 : null));
  const [values, setValues] = useState<Limits>(base?.limits ?? {});

  const body = () => ({
    name: name.trim(), description: description.trim() || null, price_cents: cents, currency, public: isPublic,
    limits: kind === "credits" ? {} : values, pages: kind === "credits" ? pages : null,
  });
  const title = "new" in mode ? `New ${KIND_LABEL[kind].one.toLowerCase()}` : "edit" in mode ? `Change the draft of ${base!.name}`
    : `Version ${base!.version + 1} of ${base!.name}`;

  return (
    <StaffForm title={title} submit={"edit" in mode ? "Save the draft" : "Create the draft"} busy="Saving…" onDone={onDone}
      ready={name.trim().length >= 2 && ("new" in mode ? /^[a-z0-9][a-z0-9-]{1,62}$/.test(key) : true) && (kind !== "credits" || !!pages)}
      onRun={(reason) => "new" in mode ? api.billing.create({ kind, key, ...body(), reason })
        : "edit" in mode ? api.billing.update(base!.id, { ...body(), reason }) : api.billing.newVersion(base!.id, { ...body(), reason })}>
      <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
        {"new" in mode && (
          <Label text="Key (stays the same across versions)">
            <input value={key} onChange={(e) => setKey(e.target.value.toLowerCase())} placeholder={kind === "credits" ? "pages-1000" : "team"} className={field} required />
          </Label>
        )}
        <Label text="Name customers see"><input value={name} onChange={(e) => setName(e.target.value)} className={field} required /></Label>
        <Label text={kind === "plan" || kind === "addon" ? "Price a month" : "Price"}><MoneyInput cents={cents} onChange={setCents} /></Label>
        <Label text="Currency">
          <input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} className={field} pattern="[A-Z]{3}" />
        </Label>
        <Label text="Description" className="@2xl:col-span-2">
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={field} placeholder="For teams reading a few thousand pages a month" />
        </Label>
        <label className="flex items-center gap-2 text-body text-ink">
          <input type="checkbox" checked={isPublic} onChange={(e) => setPublic(e.target.checked)} />
          Offered to customers (otherwise only staff give it)
        </label>
      </div>
      {kind === "credits" ? (
        <Label text="Pages in the pack">
          <input type="number" min={1} value={pages ?? ""} onChange={(e) => setPages(e.target.value ? Number(e.target.value) : null)} className={field} />
        </Label>
      ) : (
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-meta text-muted">
            {kind === "plan" ? "Limits (leave one out and it is unlimited)" : "What one unit adds"}
          </legend>
          <div className="grid grid-cols-1 gap-2 @2xl:grid-cols-2">
            {limits.map((l) => {
              const set = l.key in values;
              return (
                <div key={l.key} className="flex items-center gap-2 rounded-control bg-raised px-3 py-2">
                  <input type="checkbox" checked={set} aria-label={`Set ${l.label}`}
                    onChange={(e) => setValues((v) => { const next = { ...v }; if (e.target.checked) next[l.key] = 0; else delete next[l.key]; return next; })} />
                  <span className="min-w-0 flex-1 text-body text-ink">
                    {l.label}<span className="block text-meta text-muted">{l.scope === "organization" ? "Shared by the organization" : "Each workspace"}</span>
                  </span>
                  {set && (
                    <input type="number" min={0} value={values[l.key] ?? ""} aria-label={l.label} className={`${field} w-28`}
                      placeholder={kind === "plan" ? "Unlimited" : "0"}
                      onChange={(e) => setValues((v) => ({ ...v, [l.key]: e.target.value === "" ? (kind === "plan" ? null : 0) : Number(e.target.value) }))} />
                  )}
                </div>
              );
            })}
          </div>
        </fieldset>
      )}
    </StaffForm>
  );
}
