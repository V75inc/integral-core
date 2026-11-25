/**
 * How many entries the browser's own type and tag choices leave on screen, or
 * undefined when none is applied (tags do not apply to a pages view).
 */
export function browserFilteredCount(
  filterType: string | null | undefined,
  filterTags: readonly string[],
  isPagesView: boolean,
  shown: number,
): number | undefined {
  return filterType || (!isPagesView && filterTags.length) ? shown : undefined;
}
