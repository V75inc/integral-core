/**
 * Filters on a Track list's fields, sent to the server (``dashboard_filters``) so the totals and every page
 * follow them. ``field`` is the stored path the server filters on (``custom_fields.gross``, ``title``).
 */
export interface FieldFilter {
  field: string;
  op: string;
  value: unknown;
}

/** The server accepts at most this many filters on one request. */
export const MAX_FIELD_FILTERS = 8;

export function filterValue(filters: FieldFilter[], field: string, op: string): unknown {
  return filters.find(f => f.field === field && f.op === op)?.value;
}

/** The filters with this field and condition set to ``value`` (removed when it is empty). */
export function withFilter(filters: FieldFilter[], field: string, op: string, value: unknown): FieldFilter[] {
  const rest = filters.filter(f => !(f.field === field && f.op === op));
  const empty = value === undefined || value === null || value === '';
  return empty ? rest : [...rest, { field, op, value }];
}
