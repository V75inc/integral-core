/**
 * Unit helpers for editable_related_lines rollup math (extracted for tests).
 */

export function computeLineAmount(
  fields: Record<string, unknown>,
  quantityField?: string,
  rateField?: string,
  amountField?: string
): number {
  if (quantityField && rateField) {
    return (Number(fields[quantityField]) || 0) * (Number(fields[rateField]) || 0);
  }
  if (amountField) {
    const explicit = Number(fields[amountField]);
    if (Number.isFinite(explicit)) return explicit;
  }
  return 0;
}

export function computeSubtotal(
  lines: Array<{ fields: Record<string, unknown> }>,
  quantityField?: string,
  rateField?: string,
  amountField?: string
): number {
  return lines.reduce(
    (s, line) => s + computeLineAmount(line.fields, quantityField, rateField, amountField),
    0
  );
}

export type DiscountMode = 'percent' | 'amount';

export function computeDiscountedTotal(
  subtotal: number,
  opts: {
    mode?: DiscountMode | string | null;
    percent?: number | null;
    amount?: number | null;
  }
): { discount: number; total: number } {
  const mode = opts.mode === 'amount' ? 'amount' : 'percent';
  let discount = 0;
  if (mode === 'percent') {
    const pct = Number(opts.percent) || 0;
    discount = (subtotal * pct) / 100;
  } else {
    discount = Number(opts.amount) || 0;
  }
  if (discount < 0) discount = 0;
  if (discount > subtotal) discount = subtotal;
  return { discount, total: Math.max(0, subtotal - discount) };
}
