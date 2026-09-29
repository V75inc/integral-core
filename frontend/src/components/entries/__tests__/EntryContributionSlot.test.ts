import { describe, expect, it } from 'vitest';
import { resolveEntryContribution } from '../EntryContributionSlot';

describe('resolveEntryContribution', () => {
  it('returns the matching placement contribution', () => {
    const schema = {
      ui_contributions: [
        {
          placement: 'entry_compose',
          extension_view_key: 'document_lines',
          layout: 'wide',
        },
        {
          placement: 'entry_detail',
          extension_view_key: 'document_lines',
        },
      ],
    };
    expect(resolveEntryContribution(schema, 'entry_compose')).toEqual(
      schema.ui_contributions[0],
    );
    expect(resolveEntryContribution(schema, 'entry_detail')?.extension_view_key).toBe(
      'document_lines',
    );
  });

  it('resolves native view / view_type contributions', () => {
    const schema = {
      ui_contributions: [
        {
          placement: 'entry_compose',
          view: 'invoice_lines_editor',
          layout: 'wide',
        },
        {
          placement: 'entry_detail',
          view_type: 'editable_related_lines',
          config: { relation: 'invoice' },
        },
      ],
    };
    expect(resolveEntryContribution(schema, 'entry_compose')?.view).toBe(
      'invoice_lines_editor',
    );
    expect(resolveEntryContribution(schema, 'entry_detail')?.view_type).toBe(
      'editable_related_lines',
    );
  });

  it('returns null when missing', () => {
    expect(resolveEntryContribution(null, 'entry_compose')).toBeNull();
    expect(resolveEntryContribution({ ui_contributions: [] }, 'entry_compose')).toBeNull();
    expect(
      resolveEntryContribution(
        { ui_contributions: [{ placement: 'entry_detail', extension_view_key: 'x' }] },
        'entry_compose',
      ),
    ).toBeNull();
  });
});
