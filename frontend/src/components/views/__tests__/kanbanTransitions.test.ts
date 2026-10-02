import { describe, expect, it } from 'vitest';
import {
  boardHasTransitionPolicy,
  evaluateKanbanDrop,
  mergeKanbanColumnPolicies,
  parseKanbanColumnPolicies,
  type KanbanColumnPolicy,
} from '../kanbanTransitions';

const invoiceColumns: KanbanColumnPolicy[] = parseKanbanColumnPolicies([
  { key: 'draft', label: 'Draft' },
  {
    key: 'open',
    label: 'Open',
    accepts_from: ['draft'],
    on_drop: {
      kind: 'operation',
      operation: 'document_action',
      payload: { action: 'issue' },
    },
  },
  { key: 'partially_paid', label: 'Partially paid', drop_target: false },
  { key: 'overdue', label: 'Overdue', drop_target: false },
  {
    key: 'paid',
    label: 'Paid',
    accepts_from: ['open', 'partially_paid', 'overdue'],
    on_drop: { kind: 'receive_payment' },
  },
  { key: 'void', label: 'Void', drop_target: false },
]);

describe('parseKanbanColumnPolicies', () => {
  it('keeps transition fields on columns', () => {
    const paid = invoiceColumns.find(c => c.key === 'paid');
    expect(paid).toMatchObject({
      accepts_from: ['open', 'partially_paid', 'overdue'],
      on_drop: { kind: 'receive_payment' },
    });
    expect(paid).not.toHaveProperty('drop_target');
    expect(invoiceColumns.find(c => c.key === 'void')?.drop_target).toBe(false);
  });
});

describe('boardHasTransitionPolicy', () => {
  it('is false for plain label/key columns', () => {
    expect(
      boardHasTransitionPolicy([
        { key: 'todo', label: 'To do' },
        { key: 'done', label: 'Done' },
      ])
    ).toBe(false);
  });

  it('is true for the invoice pipeline', () => {
    expect(boardHasTransitionPolicy(invoiceColumns)).toBe(true);
  });
});

describe('evaluateKanbanDrop — invoice pipeline', () => {
  it('allows same-column reorder on display-only columns', () => {
    expect(
      evaluateKanbanDrop('partially_paid', 'partially_paid', invoiceColumns)
    ).toEqual({ action: 'reorder' });
    expect(evaluateKanbanDrop('void', 'void', invoiceColumns)).toEqual({
      action: 'reorder',
    });
  });

  it('issues on Draft → Open', () => {
    expect(evaluateKanbanDrop('draft', 'open', invoiceColumns)).toEqual({
      action: 'transition',
      on_drop: {
        kind: 'operation',
        operation: 'document_action',
        payload: { action: 'issue' },
      },
    });
  });

  it('opens receive payment on Open/Partially paid/Overdue → Paid', () => {
    for (const src of ['open', 'partially_paid', 'overdue']) {
      expect(evaluateKanbanDrop(src, 'paid', invoiceColumns)).toEqual({
        action: 'transition',
        on_drop: { kind: 'receive_payment' },
      });
    }
  });

  it('rejects drops onto Partially paid, Overdue, and Void', () => {
    expect(evaluateKanbanDrop('open', 'partially_paid', invoiceColumns).action).toBe(
      'reject'
    );
    expect(evaluateKanbanDrop('open', 'overdue', invoiceColumns).action).toBe(
      'reject'
    );
    expect(evaluateKanbanDrop('open', 'void', invoiceColumns).action).toBe(
      'reject'
    );
  });

  it('rejects Draft → Paid and Open → Draft', () => {
    expect(evaluateKanbanDrop('draft', 'paid', invoiceColumns).action).toBe(
      'reject'
    );
    expect(evaluateKanbanDrop('open', 'draft', invoiceColumns).action).toBe(
      'reject'
    );
  });

  it('keeps legacy field_write when the board has no policy', () => {
    const plain = parseKanbanColumnPolicies([
      { key: 'todo', label: 'To do' },
      { key: 'done', label: 'Done' },
    ]);
    expect(evaluateKanbanDrop('todo', 'done', plain)).toEqual({
      action: 'field_write',
    });
  });
});

describe('mergeKanbanColumnPolicies', () => {
  it('preserves on_drop when reordering columns', () => {
    const merged = mergeKanbanColumnPolicies(
      [
        { key: 'paid', label: 'Paid' },
        { key: 'open', label: 'Open' },
      ],
      invoiceColumns
    );
    expect(merged[0]).toMatchObject({
      key: 'paid',
      on_drop: { kind: 'receive_payment' },
      accepts_from: ['open', 'partially_paid', 'overdue'],
    });
    expect(merged[1]).toMatchObject({
      key: 'open',
      on_drop: {
        kind: 'operation',
        operation: 'document_action',
      },
    });
  });
});
