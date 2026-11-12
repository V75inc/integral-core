import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ColumnFilterMenu } from '../ColumnFilterMenu';
import { FilterChips } from '../FilterChips';
import { DATE_PRESETS, columnFilterSpec, columnPath, describeColumnFilter, type ColumnFilterSpec } from '../columnFilterModel';
import type { OperationalModelFieldSpec } from '../../../types';

const fields = [
  { key: 'gross', name: 'Gross', type: 'number' },
  { key: 'period', name: 'Period', type: 'text' },
  { key: 'status', name: 'Status', type: 'select', enum: ['draft', 'paid'], enum_labels: { paid: 'Settled' } },
  { key: 'pay_date', name: 'Pay date', type: 'date' },
  { key: 'employee', name: 'Employee', type: 'relation' },
] as OperationalModelFieldSpec[];
const spec = (key: string, label: string): ColumnFilterSpec => columnFilterSpec({ field: `custom_fields.${key}`, label }, fields)!;

it('works out what each column can be filtered by, and nothing for links to other records', () => {
  expect(columnPath('gross', fields)).toBe('custom_fields.gross');
  expect(columnFilterSpec({ field: 'title', label: 'Payslip' }, fields)).toMatchObject({ field: 'title', kind: 'text' });
  expect(columnFilterSpec({ field: 'custom_fields.employee', label: 'Employee' }, fields)).toBeNull();
  expect(spec('status', 'Status')).toMatchObject({ kind: 'select', options: ['draft', 'paid'] });
});

it('words each filter the way a person would say it', () => {
  const gross = spec('gross', 'Gross');
  expect(describeColumnFilter(gross, [{ field: gross.field, op: 'gte', value: 400000 }])).toBe('Gross ≥ 400,000');
  expect(describeColumnFilter(gross, [{ field: gross.field, op: 'gte', value: 200000 }, { field: gross.field, op: 'lte', value: 300000 }])).toBe('Gross: 200,000 – 300,000');
  const status = spec('status', 'Status');
  expect(describeColumnFilter(status, [{ field: status.field, op: 'in', value: ['draft', 'paid'] }])).toBe('Status: Draft, Settled');
  const period = spec('period', 'Period');
  expect(describeColumnFilter(period, [{ field: period.field, op: 'contains', value: 'May' }])).toBe('Period contains “May”');
});

it('has date presets that are real ranges', () => {
  const now = new Date(2029, 4, 15);
  const range = (key: string) => DATE_PRESETS.find(p => p.key === key)!.range(now);
  expect(range('month')).toEqual(['2029-05-01', '2029-05-31']);
  expect(range('lastmonth')).toEqual(['2029-04-01', '2029-04-30']);
  expect(range('year')).toEqual(['2029-01-01', '2029-12-31']);
  expect(range('today')).toEqual(['2029-05-15', '2029-05-15']);
});

it('applies a number range from the menu', () => {
  const onChange = vi.fn(); const onClose = vi.fn(); const gross = spec('gross', 'Gross');
  render(<ColumnFilterMenu spec={gross} filters={[]} onChange={onChange} onClose={onClose} />);
  fireEvent.change(screen.getByLabelText('Gross at least'), { target: { value: '200000' } });
  fireEvent.change(screen.getByLabelText('Gross at most'), { target: { value: '300000' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  expect(onChange).toHaveBeenCalledWith([{ field: gross.field, op: 'gte', value: 200000 }, { field: gross.field, op: 'lte', value: 300000 }]);
  expect(onClose).toHaveBeenCalled();
});

it('ticks choices, shows their labels, and clears a column', () => {
  const onChange = vi.fn(); const status = spec('status', 'Status');
  const { rerender } = render(<ColumnFilterMenu spec={status} filters={[]} onChange={onChange} onClose={vi.fn()} />);
  expect(screen.getByText('Settled')).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Settled'));
  expect(onChange).toHaveBeenLastCalledWith([{ field: status.field, op: 'in', value: ['paid'] }]);
  rerender(<ColumnFilterMenu spec={status} filters={[{ field: status.field, op: 'in', value: ['paid'] }]} onChange={onChange} onClose={vi.fn()} />);
  fireEvent.click(screen.getByLabelText('Settled'));
  expect(onChange).toHaveBeenLastCalledWith([]);
  fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
  expect(onChange).toHaveBeenLastCalledWith([]);
});

it('applies a text filter with Enter, exact or contains', () => {
  const onChange = vi.fn(); const period = spec('period', 'Period');
  render(<ColumnFilterMenu spec={period} filters={[]} onChange={onChange} onClose={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Period value'), { target: { value: ' May ' } });
  fireEvent.keyDown(screen.getByLabelText('Period value'), { key: 'Enter' });
  expect(onChange).toHaveBeenCalledWith([{ field: period.field, op: 'contains', value: 'May' }]);
  fireEvent.click(screen.getByRole('button', { name: 'Is exactly' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  expect(onChange).toHaveBeenLastCalledWith([{ field: period.field, op: 'eq', value: 'May' }]);
});

it('shows active filters as chips with a count, removes one, and clears all', () => {
  const onChange = vi.fn(); const gross = spec('gross', 'Gross'); const status = spec('status', 'Status');
  const filters = [{ field: gross.field, op: 'gte', value: 400000 }, { field: status.field, op: 'in', value: ['paid'] }];
  render(<FilterChips specs={[gross, status]} filters={filters} onChange={onChange} total={360} />);
  expect(screen.getByText('Gross ≥ 400,000')).toBeTruthy();
  expect(screen.getByText('360 results')).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Remove filter Gross'));
  expect(onChange).toHaveBeenLastCalledWith([filters[1]]);
  fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
  expect(onChange).toHaveBeenLastCalledWith([]);
});

it('shows nothing when there are no filters', () => {
  const { container } = render(<FilterChips specs={[spec('gross', 'Gross')]} filters={[]} onChange={vi.fn()} />);
  expect(container.firstChild).toBeNull();
});
