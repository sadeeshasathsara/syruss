import { humanize } from "@/lib/format";
import type { MappingPreview, SourceMapping } from "@/lib/types";

const q = (column: string) => `“${column}”`;

/**
 * A mapping in words: what each record becomes, which columns identify, name and describe it,
 * and (when there is a preview) what importing would do.
 */
export function MappingSummary({ mapping, preview }: { mapping: SourceMapping; preview?: MappingPreview[] }) {
  return (
    <div className="flex flex-col gap-3">
      {mapping.objects.map((obj) => {
        const p = preview?.find((x) => x.entity_type === obj.entity_type);
        const attributes = Object.entries(obj.attributes ?? {});
        return (
          <section key={obj.entity_type} className="rounded-control bg-raised px-3 py-2.5">
            <p className="text-body text-ink">
              Each record{mapping.records_path ? <> in {q(mapping.records_path)}</> : null} becomes a{" "}
              <span className="font-medium">{obj.entity_type}</span>
            </p>
            <dl className="mt-1.5 grid grid-cols-[minmax(96px,max-content)_minmax(0,1fr)] gap-x-3 gap-y-1">
              <dt className="text-meta text-muted">Identified by</dt>
              <dd className="min-w-0 break-words text-body text-ink">{q(obj.id_column)}</dd>
              {obj.name_column && (
                <>
                  <dt className="text-meta text-muted">Named by</dt>
                  <dd className="min-w-0 break-words text-body text-ink">{q(obj.name_column)}</dd>
                </>
              )}
              {attributes.map(([attr, column]) => (
                <div key={attr} className="contents">
                  <dt className="text-meta text-muted">{humanize(attr)}</dt>
                  <dd className="min-w-0 break-words text-body text-ink">{q(column)}</dd>
                </div>
              ))}
              {(obj.alias_columns?.length ?? 0) > 0 && (
                <>
                  <dt className="text-meta text-muted">Other spellings</dt>
                  <dd className="min-w-0 break-words text-body text-ink">{obj.alias_columns!.map(q).join(", ")}</dd>
                </>
              )}
            </dl>
            {p && (
              <p className="num mt-2 text-body text-muted">
                {[
                  `${p.new} new`,
                  p.already_known ? `${p.already_known} already known${p.names_differ ? ` (${p.names_differ} under another name)` : ""}` : null,
                  p.rows_without_id ? `${p.rows_without_id} row${p.rows_without_id === 1 ? "" : "s"} without an ID, skipped` : null,
                  p.ids_written_two_ways ? `${p.ids_written_two_ways} ID${p.ids_written_two_ways === 1 ? "" : "s"} with two different names` : null,
                ]
                  .filter(Boolean)
                  .join(", ")}
                .
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}
