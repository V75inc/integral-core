import { describe, expect, it } from 'vitest';
import { publicFormEntryTypes } from './publicFormTypeBinding';
describe('public form entry type binding', () => {
  const types = [{id:'application'}, {id:'member'}];
  it('preserves unconfigured legacy shares', () => {
    expect(publicFormEntryTypes(types)).toBe(types);
    expect(publicFormEntryTypes(types, null)).toBe(types);
  });
  it('uses only the server-bound type, even if extra schemas are returned', () => {
    expect(publicFormEntryTypes(types, 'application')).toEqual([types[0]]);
  });
  it.each(['', 'missing', ' application '])('fails closed on invalid binding %s', binding => {
    expect(() => publicFormEntryTypes(types, binding)).toThrow();
  });
  it('rejects ambiguous schemas', () => {
    expect(() => publicFormEntryTypes([types[0], types[0]], 'application')).toThrow();
  });
});
