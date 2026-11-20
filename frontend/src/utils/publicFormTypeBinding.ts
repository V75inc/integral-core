/** Presentation of a server-enforced type binding; never grants submission authority. */
export function publicFormEntryTypes<T extends { id: string }>(types: T[], binding?: string | null): T[] {
  if (binding == null) return types;
  if (typeof binding !== 'string' || !binding.trim()) throw new Error('This form has an invalid entry type binding.');
  const matches = types.filter(type => type.id === binding);
  if (matches.length !== 1) throw new Error('The entry type for this form is unavailable.');
  return matches;
}
