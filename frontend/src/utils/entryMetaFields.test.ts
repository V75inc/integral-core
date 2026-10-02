import { describe, expect, it } from 'vitest';
import {
  feedCardPrimaryNeedsExpand,
  fieldRendersAsSanitizedHtml,
  formatCustomFieldValue,
  getDisallowedCustomFieldKeys,
  getMissingRequiredFields,
  isEmptyCustomFieldValue,
  shouldRenderMetaField,
  slugEntryTypeName,
  fieldsForEntryMetaDisplay,
  sortFieldsByOrder,
} from './entryMetaFields';
import type { OperationalModelFieldSpec } from '../types';

describe('entryMetaFields', () => {
  it('slugEntryTypeName normalizes', () => {
    expect(slugEntryTypeName('Bug Report')).toBe('bug_report');
  });

  it('fieldsForEntryMetaDisplay drops text_body and moves html_body last in detail', () => {
    const fields = [
      { key: 'to', name: 'To', type: 'text' },
      { key: 'text_body', name: 'Plain text body', type: 'text', order: 900 },
      { key: 'html_body', name: 'HTML body', type: 'text', order: 901 },
      { key: 'status', name: 'Status', type: 'text' },
    ] as OperationalModelFieldSpec[];
    const values = {
      to: 'a@b.com',
      text_body: 'Plain only',
      html_body: '<p>HTML</p>',
      status: 'sent',
    };
    expect(
      fieldsForEntryMetaDisplay(fields, values, 'detail').map(f => f.key)
    ).toEqual(['to', 'status', 'html_body']);
    expect(
      fieldsForEntryMetaDisplay(fields, values, 'card').map(f => f.key)
    ).toEqual(['text_body', 'html_body', 'to', 'status']);
  });

  it('sortFieldsByOrder respects order then manifest index', () => {
    const fields = [
      { key: 'c', name: 'C', type: 'text', order: 2 },
      { key: 'a', name: 'A', type: 'text', order: 0 },
      { key: 'b', name: 'B', type: 'text', order: 1 },
    ] as OperationalModelFieldSpec[];
    expect(sortFieldsByOrder(fields).map(f => f.key)).toEqual(['a', 'b', 'c']);
    const legacy = [
      { key: 'first', name: 'First', type: 'text' },
      { key: 'second', name: 'Second', type: 'text' },
    ] as OperationalModelFieldSpec[];
    expect(sortFieldsByOrder(legacy).map(f => f.key)).toEqual(['first', 'second']);
  });

  it('fieldRendersAsSanitizedHtml detects html fields', () => {
    expect(
      fieldRendersAsSanitizedHtml({ key: 'html_body', name: 'HTML body', type: 'text' })
    ).toBe(true);
    expect(
      fieldRendersAsSanitizedHtml({ key: 'body', name: 'Body', type: 'html' })
    ).toBe(true);
    expect(
      fieldRendersAsSanitizedHtml({ key: 'note', name: 'Note', type: 'text', widget: 'html' })
    ).toBe(true);
    expect(
      fieldRendersAsSanitizedHtml({ key: 'text_body', name: 'Plain', type: 'text' })
    ).toBe(false);
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
    ] as OperationalModelFieldSpec[];
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
    ] as OperationalModelFieldSpec[];
    const missing = getMissingRequiredFields(fields, {
      start_date: '2026-06-08',
    });
    expect(missing.map(f => f.key)).toEqual(['member', 'status']);
  });

  it('formatCustomFieldValue boolean', () => {
    const f = { key: 'x', name: 'X', type: 'boolean' } as OperationalModelFieldSpec;
    expect(formatCustomFieldValue(f, true)).toBe('Yes');
    expect(formatCustomFieldValue(f, false)).toBe('No');
  });

  it('formatCustomFieldValue relation returns raw ids (RelationValue owns label resolution)', () => {
    const f = { key: 'r', name: 'R', type: 'relation' } as OperationalModelFieldSpec;
    expect(formatCustomFieldValue(f, 'id1')).toBe('id1');
    expect(formatCustomFieldValue(f, ['id1', 'id2'])).toBe('id1, id2');
    expect(formatCustomFieldValue(f, null)).toBe('');
    expect(formatCustomFieldValue(f, [])).toBe('');
  });

  it('formatCustomFieldValue member returns raw id (MemberValue owns label resolution)', () => {
    const f = { key: 'member', name: 'Member', type: 'member' } as OperationalModelFieldSpec;
    expect(formatCustomFieldValue(f, 'user-1')).toBe('user-1');
    expect(formatCustomFieldValue(f, null)).toBe('');
  });

  it('shouldRenderMetaField shows relations in both variants when value is non-empty', () => {
    const f = { key: 'r', name: 'R', type: 'relation' } as OperationalModelFieldSpec;
    // Both variants now render relations — <RelationValue> resolves ids on demand.
    expect(shouldRenderMetaField(f, 'e1', 'card')).toBe(true);
    expect(shouldRenderMetaField(f, 'e1', 'detail')).toBe(true);
    // Empty: cards stay compact; detail keeps the row so users can assign.
    expect(shouldRenderMetaField(f, null, 'card')).toBe(false);
    expect(shouldRenderMetaField(f, [], 'detail')).toBe(true);
  });

  it('shouldRenderMetaField shows member fields when value is non-empty', () => {
    const f = { key: 'member', name: 'Member', type: 'member' } as OperationalModelFieldSpec;
    expect(shouldRenderMetaField(f, 'user-1', 'card')).toBe(true);
    expect(shouldRenderMetaField(f, 'user-1', 'detail')).toBe(true);
    expect(shouldRenderMetaField(f, null, 'card')).toBe(false);
    expect(shouldRenderMetaField(f, null, 'detail')).toBe(true);
  });

  it('feedCardPrimaryNeedsExpand for long body or meta', () => {
    const fields = [
      { key: 'note', name: 'Note', type: 'text' },
    ] as OperationalModelFieldSpec[];
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
