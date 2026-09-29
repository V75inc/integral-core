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
