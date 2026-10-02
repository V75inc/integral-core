/**
 * Declarative kanban column drop policy.
 *
 * When any column on a board declares ``drop_target``, ``accepts_from``, or
 * ``on_drop``, the board runs in strict transition mode: only declared
 * cross-column moves are allowed. Boards with no policy keep the legacy
 * "write the group-by field" behavior.
 */

export type KanbanOnDropOperation = {
  kind: 'operation';
  operation: string;
  payload?: Record<string, unknown>;
};

export type KanbanOnDropReceivePayment = {
  kind: 'receive_payment';
};

export type KanbanOnDrop = KanbanOnDropOperation | KanbanOnDropReceivePayment;

export type KanbanColumnPolicy = {
  key: string;
  label?: string;
  color?: string;
  /** When false, cards cannot be dropped into this column (reorder within OK). */
  drop_target?: boolean;
  /** Source column keys allowed to drop here. */
  accepts_from?: string[];
  on_drop?: KanbanOnDrop;
};

export type KanbanDropDecision =
  | { action: 'reorder' }
  | { action: 'field_write' }
  | { action: 'transition'; on_drop: KanbanOnDrop }
  | { action: 'reject'; reason: string };

function hasPolicyFields(col: KanbanColumnPolicy): boolean {
  return (
    col.drop_target === false ||
    (Array.isArray(col.accepts_from) && col.accepts_from.length > 0) ||
    col.on_drop != null
  );
}

/** True when the board declares any column drop policy. */
export function boardHasTransitionPolicy(
  columns: KanbanColumnPolicy[]
): boolean {
  return columns.some(hasPolicyFields);
}

function normalizeOnDrop(raw: unknown): KanbanOnDrop | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const obj = raw as Record<string, unknown>;
  const kind = String(obj.kind || '').trim();
  if (kind === 'receive_payment') {
    return { kind: 'receive_payment' };
  }
  if (kind === 'operation') {
    const operation = String(obj.operation || '').trim();
    if (!operation) return undefined;
    const payload =
      obj.payload && typeof obj.payload === 'object'
        ? (obj.payload as Record<string, unknown>)
        : undefined;
    return { kind: 'operation', operation, payload };
  }
  return undefined;
}

/** Parse kanban_columns config into column policies (extra keys preserved). */
export function parseKanbanColumnPolicies(
  raw: unknown
): KanbanColumnPolicy[] {
  if (!Array.isArray(raw)) return [];
  const out: KanbanColumnPolicy[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    const key = String(row.key || '').trim();
    if (!key) continue;
    const accepts = Array.isArray(row.accepts_from)
      ? row.accepts_from.map(v => String(v || '').trim()).filter(Boolean)
      : undefined;
    const policy: KanbanColumnPolicy = {
      key,
      label: row.label != null ? String(row.label) : undefined,
      color: row.color != null ? String(row.color) : undefined,
    };
    if (row.drop_target === false) policy.drop_target = false;
    if (accepts && accepts.length) policy.accepts_from = accepts;
    const onDrop = normalizeOnDrop(row.on_drop);
    if (onDrop) policy.on_drop = onDrop;
    out.push(policy);
  }
  return out;
}

/**
 * Decide what a card drop from ``sourceCol`` onto ``targetCol`` should do.
 * Same-column drops are always ``reorder`` (order-only persist).
 */
export function evaluateKanbanDrop(
  sourceCol: string,
  targetCol: string,
  columns: KanbanColumnPolicy[]
): KanbanDropDecision {
  if (sourceCol === targetCol) {
    return { action: 'reorder' };
  }

  if (!boardHasTransitionPolicy(columns)) {
    return { action: 'field_write' };
  }

  const byKey = new Map(columns.map(c => [c.key, c]));
  const target = byKey.get(targetCol);

  if (!target) {
    return {
      action: 'reject',
      reason: 'That column is not part of this board.',
    };
  }

  if (target.drop_target === false) {
    return {
      action: 'reject',
      reason:
        targetCol === 'paid'
          ? 'Paid invoices come from recording a payment.'
          : targetCol === 'void'
            ? 'Void an invoice from its detail view (a reason is required).'
            : `Cards cannot be moved onto ${target.label || target.key}.`,
    };
  }

  if (
    Array.isArray(target.accepts_from) &&
    target.accepts_from.length > 0 &&
    !target.accepts_from.includes(sourceCol)
  ) {
    return {
      action: 'reject',
      reason: `Cannot move from ${sourceCol.replace(/_/g, ' ')} to ${
        target.label || target.key
      }.`,
    };
  }

  if (target.on_drop) {
    return { action: 'transition', on_drop: target.on_drop };
  }

  // Strict board: undeclared targets (e.g. draft) reject cross-column drops.
  if (!Array.isArray(target.accepts_from) || target.accepts_from.length === 0) {
    return {
      action: 'reject',
      reason: `Cannot move cards onto ${target.label || target.key}.`,
    };
  }

  return { action: 'field_write' };
}

/** Merge policy fields onto a column list when persisting reorder/rename. */
export function mergeKanbanColumnPolicies(
  next: Array<{ key: string; label?: string; color?: string }>,
  policies: KanbanColumnPolicy[]
): Array<Record<string, unknown>> {
  const byKey = new Map(policies.map(p => [p.key, p]));
  return next.map(col => {
    const prior = byKey.get(col.key);
    const row: Record<string, unknown> = {
      key: col.key,
      label: col.label || col.key,
    };
    if (col.color) row.color = col.color;
    else if (prior?.color) row.color = prior.color;
    if (!prior) return row;
    if (prior.drop_target === false) row.drop_target = false;
    if (prior.accepts_from?.length) row.accepts_from = [...prior.accepts_from];
    if (prior.on_drop) row.on_drop = { ...prior.on_drop };
    return row;
  });
}
