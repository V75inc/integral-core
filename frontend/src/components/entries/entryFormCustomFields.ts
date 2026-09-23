import type { OperationalModelFieldSpec, EntryTypeNode } from '../../types';
import { BASE_ENTRY_TYPE_SLUGS } from '../../utils';

export const TYPE_FIELD_CACHE_KEY = '_type_field_cache';

function readTypeFieldCache(
  customFields: Record<string, unknown>
): Record<string, Record<string, unknown>> {
  const cache = customFields[TYPE_FIELD_CACHE_KEY];
  if (!cache || typeof cache !== 'object' || Array.isArray(cache)) return {};
  const out: Record<string, Record<string, unknown>> = {};
  for (const [slug, bucket] of Object.entries(cache)) {
    if (bucket && typeof bucket === 'object' && !Array.isArray(bucket)) {
      out[slug] = bucket as Record<string, unknown>;
    }
  }
  return out;
}

/**
 * Hydrate form field values for an entry type from active custom_fields and
 * the per-type cache slot (``_type_field_cache``).
 */
export function resolveFieldValuesForEntryType(
  typeSlug: string,
  fields: OperationalModelFieldSpec[],
  customFields: Record<string, unknown>
): Record<string, unknown> {
  const cached = readTypeFieldCache(customFields)[typeSlug] || {};
  const out: Record<string, unknown> = {};

  for (const field of fields) {
    const key = field.key;
    if (!key || key.startsWith('_')) continue;
    if (field.default !== undefined) out[key] = field.default;
    const v =
      customFields[key] !== undefined ? customFields[key] : cached[key];
    if (v !== undefined) out[key] = v;
  }

  return out;
}

/**
 * Build custom_fields for create/update using only keys declared on the
 * active entry type. Preserves system ``_`` slots (including
 * ``_type_field_cache``) from baseline so dormant type-specific values
 * survive round-trip type switches.
 */
export function buildCustomFieldsForEntryType(
  dynamicFields: OperationalModelFieldSpec[],
  fieldValues: Record<string, unknown>,
  baseline?: Record<string, unknown> | null,
  extras?: Record<string, unknown> | null
): Record<string, unknown> {
  const baselineCf = baseline || {};
  const out: Record<string, unknown> = {};

  for (const [k, v] of Object.entries(baselineCf)) {
    if (k.startsWith('_')) out[k] = v;
  }

  for (const field of dynamicFields) {
    const key = field.key;
    if (!key || key.startsWith('_')) continue;
    const v =
      fieldValues[key] !== undefined ? fieldValues[key] : baselineCf[key];
    if (v !== undefined) out[key] = v;
  }

  if (extras) {
    for (const [k, v] of Object.entries(extras)) {
      if (v !== undefined) out[k] = v;
    }
  }

  return out;
}

export function slug(value: string): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

/** Canonical identity slug for an entry type: prefers the manifest's own
 *  key (`form_schema._manifest_entry_type_key`) over slugifying the
 *  display `name`. Those two only coincide when an author's chosen name
 *  happens to slug identically to the key (e.g. "Task" -> "task") — they
 *  diverge whenever it doesn't (e.g. key `paye_7b_batch`, name "Form 7B
 *  (Annual Emolument Slips)" slugs to "form_7b_annual_emolument_slips").
 *  Every caller matching a `type` state slug back to an EntryTypeNode (or
 *  building the allowed-slugs list `type` state is drawn from) MUST use
 *  this, not an ad-hoc `slug(x.name)` — the two must stay in lockstep or
 *  the "same" entry type resolves to two different slugs depending on
 *  which code path touched it, breaking view-constrained create defaults
 *  for any entry type whose name doesn't slug-match its key. Falls back to
 *  `name` for entry types materialized before `_manifest_entry_type_key`
 *  existed (rare — see `_manifest_entry_type_key` backfill in
 *  operational_model_runtime.py).
 */
export function entryTypeSlug(et: EntryTypeNode): string {
  return slug(String(et.form_schema?._manifest_entry_type_key || et.name || ''));
}

/** Allowed entry-type slugs for create mode, optionally narrowed by view constraints. */
export function filterEntryTypeSlugsForView(
  entryTypes: EntryTypeNode[],
  viewEntryTypeKeys?: string[]
): string[] {
  const slugs = entryTypes
    .map(entryTypeSlug)
    .filter(Boolean);
  let nextSlugs = slugs.length ? [...new Set(slugs)] : [...BASE_ENTRY_TYPE_SLUGS];
  const viewAllowed = (viewEntryTypeKeys || [])
    .map(k => slug(k))
    .filter(Boolean);
  if (viewAllowed.length) {
    const filtered = nextSlugs.filter(s => viewAllowed.includes(slug(s)));
    if (filtered.length) nextSlugs = filtered;
  }
  return nextSlugs;
}

/** View default → first view-allowed → track default → undefined (caller picks fallback). */
export function resolveCreateDefaultEntryType(
  allowedSlugs: string[],
  viewEntryTypeKeys: string[] | undefined,
  viewDefaultEntryTypeKey: string | undefined,
  trackDefaultEntryTypeKey: string | undefined
): string | undefined {
  const viewAllowed = (viewEntryTypeKeys || [])
    .map(k => slug(k))
    .filter(Boolean);
  const viewDefSlug = slug(String(viewDefaultEntryTypeKey || ''));
  const fromView =
    viewDefSlug && allowedSlugs.length
      ? allowedSlugs.find(s => slug(s) === viewDefSlug)
      : undefined;
  const fromViewFirstAllowed =
    !fromView && viewAllowed.length
      ? allowedSlugs.find(s => viewAllowed.includes(slug(s)))
      : undefined;
  const trackDefKey = trackDefaultEntryTypeKey;
  const fromTrack =
    !fromView && !fromViewFirstAllowed && trackDefKey && allowedSlugs.length
      ? allowedSlugs.find(s => slug(s) === slug(String(trackDefKey)))
      : undefined;
  return fromView || fromViewFirstAllowed || fromTrack;
}
