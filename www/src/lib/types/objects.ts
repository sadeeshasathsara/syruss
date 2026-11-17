// Object and thing profiles (server/app/services/object_profiles.py).
import type { DocumentStatus } from "./core";

export interface ObjectSummary {
  type: string;
  description: string;
  icon: string;
  multiple: boolean;
  identity: { id_field: string; label_field: string } | null;
  fields: number;
  documents: number;
  things: number;
  last_seen: string | null;
}

export interface ObjectField {
  name: string;
  type: string;
  required: boolean;
  description: string;
  resolves_to: string | null;
}

export interface WeekCount {
  week: string;
  count: number;
}

export interface FieldFigure {
  field: string;
  type: string;
  filled: number;
  rows: number;
  number?: { sum: number; average: number; min: number; max: number };
  range?: { first: string; last: string };
  top?: { value: string; count: number }[];
  distinct?: number;
}

export interface ProfileDocument {
  id: string;
  name: string;
  file_name: string;
  status: DocumentStatus;
  received_at: string;
  rows: number;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ThingSummary {
  key: string;
  id: string;
  label: string;
  documents: number;
  first_seen: string;
  last_seen: string;
  /** Relative to the workspace: objects/{type}/things/{id} */
  path: string;
}

export interface ObjectProfile {
  object: {
    type: string; description: string; icon: string; icon_chosen: boolean; multiple: boolean;
    identity: { id_field: string; label_field: string } | null; fields: ObjectField[];
  };
  links: Record<string, string>;
  referenced_by: { type: string; field: string }[];
  stats: { documents: number; things: number; first_seen: string | null; last_seen: string | null; rows: number; records_read: number; records_capped: boolean };
  weeks: WeekCount[];
  figures: FieldFigure[];
  things: Paged<ThingSummary> | null;
  documents: Paged<ProfileDocument>;
  screens: { slug: string; name: string }[];
}

export interface ThingProfile {
  object: { type: string; icon: string; description: string };
  thing: { key: string; id: string; label: string };
  attributes: { field: string; type: string; value: unknown; other_values: string[]; resolves_to: string | null }[];
  stats: { documents: number; first_seen: string | null; last_seen: string | null };
  weeks: WeekCount[];
  appears_with: { key: string; type: string; label: string; count: number; icon: string; path: string }[];
  knowledge: { id: string; name: string; aliases: string[]; attributes: Record<string, unknown>; created_by: string } | null;
  documents: Paged<ProfileDocument>;
  referenced_by: { type: string; field: string }[];
}

/** One row of an object with an identity as a document read it: the thing's attributes in that document. */
export interface RecordRow {
  id: string;
  document_id: string;
  document_name: string;
  file_name: string;
  status: DocumentStatus;
  seen_at: string;
  /** The thing the row is about (always there for an object with an identity) */
  thing: { key: string; id: string; label: string; path: string } | null;
  values: Record<string, unknown>;
}

export interface RecordColumn {
  name: string;
  type: string;
  /** Part of what names the thing (its id or name field) */
  identity: boolean;
  /** Whether this column belongs to a connected (resolved) object type */
  connected?: boolean;
  /** The object type this column comes from (only set when connected=true) */
  connected_type?: string;
  /** Whether this is the display label column of the connected object */
  connected_label?: boolean;
}

/** The records table: rows of an object (or of one thing) across documents, and the filters it can offer. */
export interface ObjectRecords extends Paged<RecordRow> {
  columns: RecordColumn[];
  /** Attributes with only a few different values, as filter choices (first page only) */
  facets: { field: string; values: { value: string; count: number }[] }[];
}

export interface RecordQuery {
  /** The thing's id, for one thing's rows instead of the whole object's */
  thing?: string;
  q?: string;
  filters?: Record<string, string>;
  sinceDays?: number | null;
  sort?: string;
  direction?: "asc" | "desc";
  page?: number;
}
