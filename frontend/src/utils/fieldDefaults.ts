/** Resolve declarative field ``default`` values for create forms. */

/**
 * Supported sentinels:
 * - ``$today`` / ``today`` — local calendar date as ``YYYY-MM-DD``
 */
export function resolveFieldDefault(raw: unknown, now: Date = new Date()): unknown {
  if (typeof raw !== 'string') return raw;
  const key = raw.trim().toLowerCase();
  if (key === '$today' || key === 'today') {
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }
  return raw;
}
