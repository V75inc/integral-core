import { describe, expect, it } from 'vitest';
import {
  feedCardPrimaryNeedsExpand,
  formatCustomFieldValue,
  getDisallowedCustomFieldKeys,
  getMissingRequiredFields,
  isEmptyCustomFieldValue,
  shouldRenderMetaField,
  slugEntryTypeName,
  sortFieldsByOrder,
} from './entryMetaFields';
import type { ContentProfileFieldSpec } from '../types';

describe('entryMetaFields', () => {
  it('slugEntryTypeName normalizes', () => {
    expect(slugEntryTypeName('Bug Report')).toBe('bug_report');
  });

  it('sortFieldsByOrder respects order then manifest index', () => {
    const fields = [
      { key: 'c', name: 'C', type: 'text', order: 2 },
      { key: 'a', name: 'A', type: 'text', order: 0 },
      { key: 'b', name: 'B', type: 'text', order: 1 },
    ] as ContentProfileFieldSpec[];
    expect(sortFieldsByOrder(fields).map(f => f.key)).toEqual(['a', 'b', 'c']);
    const legacy = [
      { key: 'first', name: 'First', type: 'text' },
      { key: 'second', name: 'Second', type: 'text' },
    ] as ContentProfileFieldSpec[];
    expect(sortFieldsByOrder(legacy).map(f => f.key)).toEqual(['first', 'second']);
  });

  it('isEmptyCustomFieldValue', () => {
    expect(isEmptyCustomFieldValue(null)).toBe(true);
    expect(isEmptyCustomFieldValue('')).toBe(true);
    expect(isEmptyCustomFieldValue('  ')).toBe(true);
    expect(isEmptyCustomFieldValue([])).toBe(true);
    expect(isEmptyCustomFieldValue(0)).toBe(false);
    expect(isEmptyCustomFieldValue(false)).toBe(false);
  });

  it('getDisallowedCustomFieldKeys flags keys outside schema and ignores system keys', () => {
    const fields = [
      { key: 'publish_date', name: 'Publish date', type: 'date' },
    ] as ContentProfileFieldSpec[];
    expect(
      getDisallowedCustomFieldKeys(fields, {
        publish_date: '2026-06-09',
        _entry_type_slug: 'contentpiece',
      })
    ).toEqual([]);
    expect(
      getDisallowedCustomFieldKeys([], { publish_date: '2026-06-09' })
    ).toEqual(['publish_date']);
  });

  it('getMissingRequiredFields lists only required fields without values', () => {
    const fields = [
      { key: 'member', name: 'Member', type: 'member', required: true },
      { key: 'start_date', name: 'Start', type: 'date' },
      { key: 'status', name: 'Status', type: 'select', required: true },
    ] as ContentProfileFieldSpec[];
    const missing = getMissingRequiredFields(fields, {
      start_date: '2026-06-08',
    });
    expect(missing.map(f => f.key)).toEqual(['member', 'status']);
  });

  it('formatCustomFieldValue boolean', () => {
    const f = { key: 'x', name: 'X', type: 'boolean' } as ContentProfileFieldSpec;
    expect(formatCustomFieldValue(f, true)).toBe('Yes');
    expect(formatCustomFieldValue(f, false)).toBe('No');
  });

  it('formatCustomFieldValue relation returns raw ids (RelationValue owns label resolution)', () => {
    const f = { key: 'r', name: 'R', type: 'relation' } as ContentProfileFieldSpec;
    expect(formatCustomFieldValue(f, 'id1')).toBe('id1');
    expect(formatCustomFieldValue(f, ['id1', 'id2'])).toBe('id1, id2');
    expect(formatCustomFieldValue(f, null)).toBe('');
    expect(formatCustomFieldValue(f, [])).toBe('');
  });

  it('formatCustomFieldValue member returns raw id (MemberValue owns label resolution)', () => {
    const f = { key: 'member', name: 'Member', type: 'member' } as ContentProfileFieldSpec;
    expect(formatCustomFieldValue(f, 'user-1')).toBe('user-1');
    expect(formatCustomFieldValue(f, null)).toBe('');
  });

  it('shouldRenderMetaField shows relations in both variants when value is non-empty', () => {
    const f = { key: 'r', name: 'R', type: 'relation' } as ContentProfileFieldSpec;
    // Both variants now render relations — <RelationValue> resolves ids on demand.
    expect(shouldRenderMetaField(f, 'e1', 'card')).toBe(true);
    expect(shouldRenderMetaField(f, 'e1', 'detail')).toBe(true);
    // Empty: cards stay compact; detail keeps the row so users can assign.
    expect(shouldRenderMetaField(f, null, 'card')).toBe(false);
    expect(shouldRenderMetaField(f, [], 'detail')).toBe(true);
  });

  it('shouldRenderMetaField shows member fields when value is non-empty', () => {
    const f = { key: 'member', name: 'Member', type: 'member' } as ContentProfileFieldSpec;
    expect(shouldRenderMetaField(f, 'user-1', 'card')).toBe(true);
    expect(shouldRenderMetaField(f, 'user-1', 'detail')).toBe(true);
    expect(shouldRenderMetaField(f, null, 'card')).toBe(false);
    expect(shouldRenderMetaField(f, null, 'detail')).toBe(true);
  });

  it('feedCardPrimaryNeedsExpand for long body or meta', () => {
    const fields = [
      { key: 'note', name: 'Note', type: 'text' },
    ] as ContentProfileFieldSpec[];
    expect(
      feedCardPrimaryNeedsExpand({
        body: 'x'.repeat(201),
        fields,
        values: {},
      })
    ).toBe(true);
    expect(
      feedCardPrimaryNeedsExpand({
        body: 'short',
        title: 'y'.repeat(130),
        fields,
        values: {},
      })
    ).toBe(true);
    expect(
      feedCardPrimaryNeedsExpand({
        body: 'short',
        fields,
        values: { note: 'z'.repeat(120) },
      })
    ).toBe(true);
    expect(
      feedCardPrimaryNeedsExpand({
        body: 'short',
        fields,
        values: { note: 'hi' },
      })
    ).toBe(false);
  });
});
