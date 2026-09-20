import { describe, expect, it } from 'vitest';
import { resolveEntryFieldValue } from '../entryFieldValue';

describe('resolveEntryFieldValue', () => {
  it('keeps platform and business status values in their declared namespaces', () => {
    const entry = { status: 'active', custom_fields: { status: null } };

    expect(resolveEntryFieldValue(entry, 'status')).toBe('active');
    expect(resolveEntryFieldValue(entry, 'custom_fields.status')).toBeNull();
  });

  it('retains bare-key compatibility only for unreserved business keys', () => {
    const entry = { title: 'Truck 01', custom_fields: { registration: 'PZZ 1234' } };

    expect(resolveEntryFieldValue(entry, 'registration')).toBe('PZZ 1234');
    expect(resolveEntryFieldValue(entry, 'custom_fields.registration')).toBe('PZZ 1234');
  });
});
