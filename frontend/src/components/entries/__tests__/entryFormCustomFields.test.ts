import { describe, expect, it } from 'vitest';
import {
  buildCustomFieldsForEntryType,
  resolveFieldValuesForEntryType,
} from '../entryFormCustomFields';
import type { ContentProfileFieldSpec } from '../../../types';

describe('buildCustomFieldsForEntryType', () => {
  const ideaFields: ContentProfileFieldSpec[] = [
    { key: 'category', name: 'Category', type: 'select' },
    { key: 'status', name: 'Status', type: 'select' },
  ];

  it('drops baseline keys not on the active entry type', () => {
    const out = buildCustomFieldsForEntryType(
      ideaFields,
      { category: 'product' },
      { source: 'web', category: 'research', status: 'draft' }
    );
    expect(out).toEqual({ category: 'product', status: 'draft' });
    expect(out).not.toHaveProperty('source');
  });

  it('preserves internal keys from baseline', () => {
    const out = buildCustomFieldsForEntryType(
      ideaFields,
      {},
      { _link_preview: { url: 'https://x.test' }, source: 'x' }
    );
    expect(out._link_preview).toEqual({ url: 'https://x.test' });
    expect(out).not.toHaveProperty('source');
  });

  it('preserves _type_field_cache from baseline', () => {
    const cache = { qb_invoice: { invoice_number: 'INV-9' } };
    const out = buildCustomFieldsForEntryType(ideaFields, { category: 'x' }, {
      _type_field_cache: cache,
      category: 'old',
    });
    expect(out._type_field_cache).toEqual(cache);
    expect(out.category).toBe('x');
  });
});

describe('resolveFieldValuesForEntryType', () => {
  const invoiceFields: ContentProfileFieldSpec[] = [
    { key: 'invoice_number', name: 'Invoice #', type: 'text' },
    { key: 'amount', name: 'Amount', type: 'number' },
  ];

  it('hydrates from active custom_fields and per-type cache', () => {
    const values = resolveFieldValuesForEntryType('qb_invoice', invoiceFields, {
      amount: 10,
      _type_field_cache: { qb_invoice: { invoice_number: 'INV-1' } },
    });
    expect(values).toEqual({ amount: 10, invoice_number: 'INV-1' });
  });
});
