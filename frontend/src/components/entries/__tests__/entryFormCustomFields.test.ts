import { describe, expect, it } from 'vitest';
import {
  buildCustomFieldsForEntryType,
  diffCustomFields,
  resolveFieldValuesForEntryType,
  valuesEqualForDiff,
} from '../entryFormCustomFields';
import type { OperationalModelFieldSpec } from '../../../types';

describe('buildCustomFieldsForEntryType', () => {
  const ideaFields: OperationalModelFieldSpec[] = [
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

describe('buildCustomFieldsForEntryType (changedOnly)', () => {
  const fields: OperationalModelFieldSpec[] = [
    { key: 'pipeline_status', name: 'Pipeline status', type: 'select' },
    { key: 'owner', name: 'Owner', type: 'member' },
    { key: 'notes', name: 'Notes', type: 'text' },
    { key: 'related', name: 'Related', type: 'relation' },
  ];

  it('omits unchanged fields (incl. protected) and includes changed ones', () => {
    const out = buildCustomFieldsForEntryType(
      fields,
      { pipeline_status: 'contacted' },
      { pipeline_status: 'new', owner: 'n.User.1', notes: 'hi' },
      null,
      { changedOnly: true }
    );
    expect(out).toEqual({ pipeline_status: 'contacted' });
    expect(out).not.toHaveProperty('owner');
    expect(out).not.toHaveProperty('notes');
  });

  it('includes a cleared field', () => {
    const out = buildCustomFieldsForEntryType(
      fields,
      { owner: '' },
      { owner: 'n.User.1' },
      null,
      { changedOnly: true }
    );
    expect(out).toHaveProperty('owner', '');
  });

  it('ignores benign representation diffs (number/string, array order)', () => {
    const out = buildCustomFieldsForEntryType(
      [
        { key: 'amount', name: 'Amount', type: 'number' },
        { key: 'related', name: 'Related', type: 'relation' },
      ],
      { amount: 42, related: ['b', 'a'] },
      { amount: '42', related: ['a', 'b'] },
      null,
      { changedOnly: true }
    );
    expect(out).toEqual({});
  });

  it('includes all fields when changedOnly is not set (create mode)', () => {
    const out = buildCustomFieldsForEntryType(fields, { pipeline_status: 'new' }, {
      owner: 'n.User.1',
      notes: 'hi',
    });
    expect(out).toEqual({
      pipeline_status: 'new',
      owner: 'n.User.1',
      notes: 'hi',
    });
  });
});

describe('diffCustomFields', () => {
  it('drops unchanged keys (incl. a protected-style owner), keeps changed ones', () => {
    const out = diffCustomFields(
      { pipeline_status: 'contacted', owner: 'n.User.1', _kanban_order: 2 },
      { pipeline_status: 'new', owner: 'n.User.1', _kanban_order: 1 }
    );
    expect(out).toEqual({ pipeline_status: 'contacted', _kanban_order: 2 });
    expect(out).not.toHaveProperty('owner');
  });

  it('keeps cleared values and new keys', () => {
    expect(diffCustomFields({ owner: '' }, { owner: 'n.User.1' })).toEqual({
      owner: '',
    });
    expect(diffCustomFields({ fresh: 'x' }, {})).toEqual({ fresh: 'x' });
  });

  it('drops benign representation diffs', () => {
    expect(
      diffCustomFields(
        { amount: 42, related: ['b', 'a'] },
        { amount: '42', related: ['a', 'b'] }
      )
    ).toEqual({});
  });
});

describe('valuesEqualForDiff', () => {
  it('treats empty representations as equal', () => {
    expect(valuesEqualForDiff(null, undefined)).toBe(true);
    expect(valuesEqualForDiff('', null)).toBe(true);
  });

  it('detects real changes', () => {
    expect(valuesEqualForDiff('a', 'b')).toBe(false);
    expect(valuesEqualForDiff(['a'], ['b'])).toBe(false);
  });

  it('ignores numeric string coercion and array order', () => {
    expect(valuesEqualForDiff(42, '42')).toBe(true);
    expect(valuesEqualForDiff(['a', 'b'], ['b', 'a'])).toBe(true);
  });
});

describe('resolveFieldValuesForEntryType', () => {
  const invoiceFields: OperationalModelFieldSpec[] = [
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
