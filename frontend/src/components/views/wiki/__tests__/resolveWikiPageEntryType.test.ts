import { describe, expect, it } from 'vitest';
import {
  getWikiViewConstraints,
  resolveWikiPageEntryType,
} from '../resolveWikiPageEntryType';
import type { SavedView } from '../../../../types';

describe('resolveWikiPageEntryType', () => {
  it('prefers page when listed on the view', () => {
    const view = {
      id: 'v1',
      name: 'Pages',
      type: 'wiki',
      track_id: 't1',
      entry_type_keys: ['page'],
      default_entry_type_key: 'page',
    } as SavedView;
    expect(resolveWikiPageEntryType(view)).toBe('page');
  });

  it('reads constraints from view config when node fields are empty', () => {
    const view = {
      id: 'v1',
      name: 'Pages',
      type: 'wiki',
      track_id: 't1',
      config: {
        entry_type_keys: ['page'],
        default_entry_type: 'page',
        parent_field: 'parent',
      },
    } as SavedView;
    expect(getWikiViewConstraints(view).entryTypeKeys).toEqual(['page']);
    expect(resolveWikiPageEntryType(view)).toBe('page');
  });

  it('does not fall back to track note default when view is unconstrained', () => {
    const view = {
      id: 'v1',
      name: 'Pages',
      type: 'wiki',
      track_id: 't1',
    } as SavedView;
    expect(resolveWikiPageEntryType(view)).toBe('page');
  });
});
