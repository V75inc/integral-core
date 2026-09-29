import { describe, expect, it } from 'vitest';
import {
  computeDiscountedTotal,
  computeLineAmount,
  computeSubtotal,
} from '../editableRelatedLinesMath';

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

  it('applies percent and amount discounts', () => {
    expect(computeDiscountedTotal(100, { mode: 'percent', percent: 10 })).toEqual({
      discount: 10,
      total: 90,
    });
    expect(computeDiscountedTotal(100, { mode: 'amount', amount: 25 })).toEqual({
      discount: 25,
      total: 75,
    });
  });
});
