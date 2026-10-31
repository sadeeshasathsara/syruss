"use client";

import Link from "next/link";
import { useState } from "react";
import { ImageViewer, type ViewerImage } from "@/components/ui/ImageViewer";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { display, humanize, percent, STATUS_LABEL, STATUS_TONE } from "@/lib/format";
import type { DocumentStatus } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import type { RunStep } from "./timeline";

type Json = Record<string, unknown>;

/** Telemetry shortens long values, so a result may be cut off mid-JSON: parse when we can. */
function parse(text?: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** One string argument, read even from arguments that were cut off. */
function argOf(args: Json, rawArgs: string | undefined, key: string): string {
  if (typeof args[key] === "string") return args[key] as string;
  const m = new RegExp(String.raw`"${key}":\s*"((?:[^"\\]|\\.)*)`).exec(rawArgs ?? "");
  if (!m) return "";
  try {
    return JSON.parse(`"${m[1]}"`);
  } catch {
    return m[1];
  }
}

const isObj = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown) => (typeof v === "string" ? v : v === undefined || v === null ? "" : String(v));

/** Documents in a (possibly cut-off) result: each `{"id": ...}` chunk with its file name and status. */
function documentsIn(result: string): { id: string; filename: string; status?: string; role?: string }[] {
  const parsed = parse(result);
  const list = Array.isArray(parsed) ? parsed : isObj(parsed) ? [parsed] : null;
  if (list) {
    return list.filter(isObj).filter((d) => d.id && d.filename).map((d) => ({
      id: text(d.id), filename: text(d.filename), status: text(d.status) || undefined, role: text(d.role) || undefined,
    }));
  }
  return result.split(/\{"id":\s*"/).slice(1).flatMap((chunk) => {
    const id = chunk.slice(0, chunk.indexOf('"'));
    const filename = /"filename":\s*"([^"]+)"/.exec(chunk)?.[1];
    if (!id || !filename) return [];
    return [{ id, filename, status: /"status":\s*"([^"]+)"/.exec(chunk)?.[1], role: /"role":\s*"([^"]+)"/.exec(chunk)?.[1] }];
  });
}

const LABEL_KEYS = ["filename", "name", "original_filename", "title", "question", "content", "message", "observation", "text"];

/** What any other result says, as a short list or key/value lines rather than raw JSON. */
function Summary({ value, raw }: { value: unknown; raw?: string }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <p className="text-body text-muted">Nothing found.</p>;
    return (
      <ul className="flex flex-col gap-1">
        {value.slice(0, 8).map((item, i) => {
          const label = isObj(item) ? text(LABEL_KEYS.map((k) => item[k]).find(Boolean)) : display(item);
          const kind = isObj(item) ? text(item.kind || item.entity_type || item.status) : "";
          return (
            <li key={i} className="text-body text-ink">
              {label || `Item ${i + 1}`}
              {kind && <span className="ml-2 text-meta text-muted">{humanize(kind)}</span>}
            </li>
          );
        })}
        {value.length > 8 && <li className="text-meta text-muted">and {value.length - 8} more</li>}
      </ul>
    );
  }
  if (isObj(value)) {
    const rows = Object.entries(value).filter(([, v]) => v !== null && v !== "" && (typeof v !== "object" || Array.isArray(v)));
    if (rows.length === 0) return <p className="text-body text-muted">Done.</p>;
    return (
      <dl className="grid grid-cols-[minmax(80px,max-content)_minmax(0,1fr)] gap-x-3 gap-y-1">
        {rows.slice(0, 10).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-meta text-muted">{humanize(k)}</dt>
            <dd className="min-w-0 break-words text-body text-ink">
              {Array.isArray(v) ? `${v.length} item${v.length === 1 ? "" : "s"}` : display(v)}
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  if (!raw) return <p className="text-body text-muted">No result yet.</p>;
  if (/^(REFUSED|ERROR)/.test(raw)) return <p className="break-words text-body text-negative">{raw}</p>;
  if (/^[[{]/.test(raw.trim())) {
    const labels = [...raw.matchAll(/"(?:filename|name|content|question|message)":\s*"([^"]{1,120})"/g)].map((m) => m[1]);
    return labels.length ? <Summary value={labels} /> : <p className="text-body text-muted">Too long to show here.</p>;
  }
  return <p className="line-clamp-6 whitespace-pre-wrap break-words text-body text-ink">{raw}</p>;
}

/**
 * What one step of the agent's work found, in the terms of the workspace: the documents it
 * looked up, the page it looked at (and where it zoomed), the matches, the checks.
 */
export function StepDetail({ step }: { step: RunStep }) {
  const { workspace, href } = useWorkspace();
  const [viewing, setViewing] = useState<number | null>(null);
  const args = (parse(step.args) ?? {}) as Json;
  const result = parse(step.result);
  const file = (id: string) => api.documents.fileUrl(workspace.id, id);
  const arg = (key: string) => argOf(args, step.args, key);

  if (step.result?.startsWith("REFUSED") || step.result?.startsWith("ERROR")) return <Summary value={undefined} raw={step.result} />;

  switch (step.tool) {
    case "save_screen":
    case "update_screen": {
      const saved = isObj(result) ? result : undefined;
      const slug = text(saved?.slug) || arg("slug");
      const suggested = saved?.status === "suggested";
      return (
        <div className="flex flex-col gap-2">
          <p className="text-body text-ink">{text(saved?.name) || arg("name")}{suggested ? " (a suggestion, waiting for someone to keep it)" : ""}</p>
          {slug && (
            <Link href={href(suggested ? "analytics/suggested" : `analytics/${slug}`)} className="w-fit text-body font-medium text-ink underline underline-offset-2">
              {suggested ? "See the suggestion" : "Open the screen"}
            </Link>
          )}
        </div>
      );
    }

    case "view_document": {
      const id = arg("document_id");
      const r = Array.isArray(args.region) && args.region.length === 4 ? (args.region as number[]) : null;
      const images: ViewerImage[] = id ? [{ src: file(id), title: "The page the agent looked at" }] : [];
      return (
        <div className="flex flex-col gap-2">
          {arg("purpose") && <p className="text-body text-ink">{arg("purpose").replace(/^\w/, (c) => c.toUpperCase())}</p>}
          {id && (
            <button
              type="button"
              onClick={() => setViewing(0)}
              aria-label="Open the page full size"
              className="relative w-fit overflow-hidden rounded-control border border-border bg-raised transition-transform duration-150 hover:-translate-y-0.5"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={file(id)} alt="" loading="lazy" className="block max-h-56 w-auto max-w-full" />
              {r && (
                // The zoom box, on the agent's 0-1000 scale: [ymin, xmin, ymax, xmax].
                <span
                  aria-hidden
                  className="absolute rounded-[3px] border-2 border-accent bg-accent/10"
                  style={{ top: `${r[0] / 10}%`, left: `${r[1] / 10}%`, height: `${(r[2] - r[0]) / 10}%`, width: `${(r[3] - r[1]) / 10}%` }}
                />
              )}
            </button>
          )}
          <ImageViewer images={images} index={viewing} onIndexChange={setViewing} />
        </div>
      );
    }

    case "list_documents":
    case "get_document": {
      const docs = documentsIn(step.result ?? "");
      if (docs.length === 0) return <Summary value={result} raw={step.result} />;
      const images = docs.map((d) => ({ src: file(d.id), title: d.filename }));
      return (
        <>
          <ul className="flex flex-col gap-1.5">
            {docs.map((d, i) => (
              <li key={d.id} className="flex min-w-0 items-center gap-2.5">
                <button
                  type="button"
                  onClick={() => setViewing(i)}
                  aria-label={`Open ${d.filename}`}
                  className="size-9 flex-none overflow-hidden rounded-chip border border-border bg-raised transition-transform duration-150 hover:scale-105"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={file(d.id)} alt="" loading="lazy" className="size-full object-cover" />
                </button>
                {d.role === "SAMPLE" || workspace.status === "draft" ? (
                  <span className="min-w-0 flex-1 truncate text-body text-ink" title={d.filename}>{d.filename}</span>
                ) : (
                  <Link href={href(`documents/${d.id}`)} className="min-w-0 flex-1 truncate text-body text-ink hover:underline" title={d.filename}>
                    {d.filename}
                  </Link>
                )}
                {d.role === "SAMPLE" ? (
                  <Pill tone="neutral">Sample</Pill>
                ) : (
                  d.status && STATUS_LABEL[d.status as DocumentStatus] && (
                    <Pill tone={STATUS_TONE[d.status as DocumentStatus]}>{STATUS_LABEL[d.status as DocumentStatus]}</Pill>
                  )
                )}
              </li>
            ))}
          </ul>
          <ImageViewer images={images} index={viewing} onIndexChange={setViewing} />
        </>
      );
    }

    case "query_records": {
      if (!isObj(result)) return <Summary value={result} raw={step.result} />;
      const distinct = Array.isArray(result.distinct) ? result.distinct.filter(isObj) : null;
      return (
        <div className="flex flex-col gap-1.5">
          <p className="text-body text-muted">
            {text(result.rows_matched)} matching rows in {text(result.documents_matched)} of {text(result.documents_searched)} saved records
          </p>
          {distinct && distinct.length > 0 && (
            <ul className="flex flex-col gap-0.5">
              {distinct.slice(0, 12).map((g, i) => (
                <li key={i} className="flex min-w-0 items-baseline gap-2 text-body">
                  <span className="min-w-0 truncate text-ink">
                    {isObj(g.values) ? Object.values(g.values).map(text).filter(Boolean).join(" · ") : ""}
                  </span>
                  <span className="flex-none text-meta text-muted">{text(g.rows)}×</span>
                </li>
              ))}
              {distinct.length > 12 && <li className="text-meta text-muted">and {distinct.length - 12} more</li>}
            </ul>
          )}
          {typeof result.hint === "string" && <p className="text-meta text-muted">{result.hint}</p>}
        </div>
      );
    }

    case "search_entities": {
      const candidates = isObj(result) && Array.isArray(result.candidates) ? result.candidates.filter(isObj) : null;
      if (!candidates) return <Summary value={result} raw={step.result} />;
      if (candidates.length === 0) return <p className="text-body text-muted">No known match.</p>;
      return (
        <ul className="flex flex-col gap-1">
          {candidates.slice(0, 6).map((c, i) => (
            <li key={i} className="flex min-w-0 items-baseline gap-2">
              <span className="min-w-0 truncate text-body text-ink">{text(c.name)}</span>
              <span className="min-w-0 truncate text-meta text-muted">
                {[humanize(text(c.entity_type)), text(c.identifier_value)].filter(Boolean).join(" · ")}
              </span>
              {typeof c.score === "number" && <span className="num ml-auto flex-none text-meta text-muted">{percent(c.score)} match</span>}
            </li>
          ))}
        </ul>
      );
    }

    case "validate_record": {
      if (!isObj(result) || typeof result.valid !== "boolean") return <Summary value={result} raw={step.result} />;
      const errors = Array.isArray(result.errors) ? result.errors.filter(isObj) : [];
      return result.valid ? (
        <p className="text-body text-positive">Fits the template{result.rules_checked ? ` and passes ${result.rules_checked} check${result.rules_checked === 1 ? "" : "s"}` : ""}.</p>
      ) : (
        <ul className="flex list-disc flex-col gap-1 pl-5">
          {errors.slice(0, 6).map((e, i) => (
            <li key={i} className="text-body text-ink">{text(e.message)}</li>
          ))}
        </ul>
      );
    }

    case "calculate":
      return isObj(result) ? (
        <p className="num text-body text-ink">{text(result.expression)} = <span className="font-medium">{display(result.result)}</span></p>
      ) : (
        <Summary value={result} raw={step.result} />
      );

    case "submit_record":
      return (
        <p className="text-body text-ink">
          {text(args.decision) === "REVIEW" ? "Sent to review" : "Committed"}
          {typeof args.confidence === "number" && <span className="text-muted">, {percent(args.confidence)} sure</span>}
          {arg("review_reason") && <span className="mt-1 block text-muted">{arg("review_reason")}</span>}
        </p>
      );

    case "reply_to_user":
      // A preview of the reply (shown in full in the thread), without markdown symbols.
      return <p className="line-clamp-4 whitespace-pre-wrap text-body text-ink">{arg("content").replace(/\*\*|__|`/g, "")}</p>;
    case "ask_human":
      return <p className="text-body text-ink">{arg("question")}</p>;
    case "remember":
      return <p className="text-body text-ink">{arg("content")}</p>;
    case "propose_template_change":
      return (
        <p className="text-body text-ink">
          {arg("change_summary") || arg("observation") || "A new version of the template."}
          <span className="mt-1 block text-muted">The full proposal is in its own card below.</span>
        </p>
      );
    default:
      return <Summary value={result} raw={step.result} />;
  }
}
