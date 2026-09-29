import { describe, expect, it } from 'vitest';
import { computeLineAmount, computeSubtotal } from '../editableRelatedLinesMath';

describe('editableRelatedLinesMath', () => {
  it('computes qty * rate', () => {
    expect(
      computeLineAmount(
        { quantity: 2, unit_price: 50 },
        'quantity',
        'unit_price',
        'line_amount'
      )
    ).toBe(100);
  });

  it('sums lines for parent total', () => {
    expect(
      computeSubtotal(
        [
          { fields: { quantity: 1, unit_price: 10 } },
          { fields: { quantity: 3, unit_price: 20 } },
        ],
        'quantity',
        'unit_price',
        'line_amount'
      )
    ).toBe(70);
  });
});
