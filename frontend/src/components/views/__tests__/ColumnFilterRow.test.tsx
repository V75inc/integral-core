import { render, screen, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ColumnFilterRow, columnFilterSpec, columnPath } from '../ColumnFilterRow';
import { withFilter, type FieldFilter } from '../../tracks/fieldFilters';
import type { OperationalModelFieldSpec } from '../../../types';

const fields = [
  { key: 'gross', name: 'Gross', type: 'number' },
  { key: 'period', name: 'Period', type: 'text' },
  { key: 'status', name: 'Status', type: 'select', enum: ['draft', 'paid'] },
  { key: 'pay_date', name: 'Pay date', type: 'date' },
  { key: 'employee', name: 'Employee', type: 'relation' },
] as OperationalModelFieldSpec[];
const columns = [
  { field: 'title', label: 'Payslip' },
  { field: 'custom_fields.period', label: 'Period' },
  { field: 'custom_fields.gross', label: 'Gross' },
  { field: 'custom_fields.status', label: 'Status' },
  { field: 'custom_fields.pay_date', label: 'Pay date' },
  { field: 'custom_fields.employee', label: 'Employee' },
];

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

function mount(onChange = vi.fn(), filters: FieldFilter[] = []) {
  render(<table><thead><ColumnFilterRow columns={columns} fields={fields} filters={filters} onChange={onChange} /></thead></table>);
  return onChange;
}

it('stores a filter on each column under the path the server filters on', () => {
  expect(columnPath('gross', fields)).toBe('custom_fields.gross');
  expect(columnPath('custom_fields.gross', fields)).toBe('custom_fields.gross');
  expect(columnFilterSpec({ field: 'title' }, fields)?.field).toBe('title');
  expect(columnFilterSpec({ field: 'custom_fields.employee' }, fields)).toBeNull();
});

it('filters text, after typing stops, with a contains condition', () => {
  const onChange = mount();
  fireEvent.change(screen.getByLabelText('Filter Period'), { target: { value: 'May 2029' } });
  expect(onChange).not.toHaveBeenCalled();
  act(() => { vi.advanceTimersByTime(500); });
  expect(onChange).toHaveBeenCalledWith([{ field: 'custom_fields.period', op: 'contains', value: 'May 2029' }]);
});

it('filters a number by min and max, and removes a bound when it is cleared', () => {
  const onChange = mount(vi.fn(), [{ field: 'custom_fields.gross', op: 'gte', value: 400000 }]);
  fireEvent.change(screen.getByLabelText('Gross at most'), { target: { value: '900000' } });
  act(() => { vi.advanceTimersByTime(500); });
  expect(onChange).toHaveBeenCalledWith([
    { field: 'custom_fields.gross', op: 'gte', value: 400000 },
    { field: 'custom_fields.gross', op: 'lte', value: 900000 },
  ]);
  fireEvent.change(screen.getByLabelText('Gross at least'), { target: { value: '' } });
  act(() => { vi.advanceTimersByTime(500); });
  expect(onChange).toHaveBeenLastCalledWith([]);
});

it('filters a choice field and shows no control for links to other records', () => {
  const onChange = mount();
  fireEvent.change(screen.getByLabelText('Filter Status'), { target: { value: 'paid' } });
  expect(onChange).toHaveBeenCalledWith([{ field: 'custom_fields.status', op: 'eq', value: 'paid' }]);
  expect(screen.queryByLabelText('Filter Employee')).toBeNull();
});

it('withFilter replaces one condition and drops an empty one', () => {
  const a = withFilter([], 'x', 'eq', 1);
  expect(withFilter(a, 'x', 'eq', 2)).toEqual([{ field: 'x', op: 'eq', value: 2 }]);
  expect(withFilter(a, 'x', 'eq', '')).toEqual([]);
  expect(withFilter([], 'x', 'eq', false)).toEqual([{ field: 'x', op: 'eq', value: false }]);
});
