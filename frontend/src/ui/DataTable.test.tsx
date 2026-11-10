import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DataTable, type DataTableColumn } from './DataTable';
import { Tabs } from './Tabs';
import { StatusBadge } from './StatusBadge';

type Row = { id: string; name: string; gross: number | null };
const rows: Row[] = [
  { id: 'a', name: 'Cara', gross: 300 }, { id: 'b', name: 'Ann', gross: 1200 }, { id: 'c', name: 'Bob', gross: null }, { id: 'd', name: 'Dee', gross: 50 },
];
const columns: DataTableColumn<Row>[] = [
  { key: 'name', label: 'Name', sortable: true, pinned: true, width: '8rem' },
  { key: 'gross', label: 'Gross', sortable: true, align: 'end', render: r => (r.gross == null ? '' : r.gross.toFixed(2)), total: '1,550.00' },
];
const names = () => screen.getAllByRole('row').slice(1, -1).map(r => within(r).getAllByRole('cell')[0].textContent);

describe('DataTable', () => {
  it('shows every row, names the table, right-aligns amounts and shows the totals row', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={r => r.id} caption="Register" />);
    expect(screen.getByRole('table', { name: 'Register' })).toBeTruthy();
    expect(names()).toEqual(['Cara', 'Ann', 'Bob', 'Dee']);
    expect(screen.getByText('1200.00').className).toContain('text-right');
    expect(screen.getByText('1,550.00')).toBeTruthy();
  });

  it('sorts on the header, descending on a second click, and keeps empty values last', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={r => r.id} caption="Register" />);
    fireEvent.click(screen.getByRole('button', { name: 'Gross' }));
    expect(names()).toEqual(['Dee', 'Cara', 'Ann', 'Bob']);
    fireEvent.click(screen.getByRole('button', { name: 'Gross' }));
    expect(names()).toEqual(['Ann', 'Cara', 'Dee', 'Bob']);
    expect(screen.getByRole('columnheader', { name: 'Gross' }).getAttribute('aria-sort')).toBe('descending');
    fireEvent.click(screen.getByRole('button', { name: 'Gross' }));
    expect(names()).toEqual(['Cara', 'Ann', 'Bob', 'Dee']); // third click clears the sort
  });

  it('pages, and goes back to the first page when the sort changes', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={r => r.id} caption="Register" pageSize={3} />);
    expect(screen.getByText('Showing 1–3 of 4')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Showing 4–4 of 4')).toBeTruthy();
    expect(names()).toEqual(['Dee']);
    fireEvent.click(screen.getByRole('button', { name: 'Name' }));
    expect(screen.getByText('Showing 1–3 of 4')).toBeTruthy();
    expect(names()).toEqual(['Ann', 'Bob', 'Cara']);
  });

  it('pins the leading column and opens a row on click or Enter', () => {
    const open = vi.fn();
    render(<DataTable columns={columns} rows={rows} rowKey={r => r.id} caption="Register" onRowClick={open} />);
    const first = screen.getAllByRole('row')[1];
    expect(within(first).getAllByRole('cell')[0].style.position).toBe('sticky');
    fireEvent.click(first);
    fireEvent.keyDown(first, { key: 'Enter' });
    expect(open).toHaveBeenCalledTimes(2);
    expect(open.mock.calls[0][0]).toMatchObject({ id: 'a' });
  });

  it('says so when there are no rows', () => {
    render(<DataTable columns={columns} rows={[]} rowKey={r => r.id} caption="Register" empty="No one is on this run." />);
    expect(screen.getByText('No one is on this run.')).toBeTruthy();
  });
});

describe('Tabs and StatusBadge', () => {
  it('switches tabs and shows counts', () => {
    const change = vi.fn();
    render(<Tabs ariaLabel="Sections" options={[{ value: 'register', label: 'Register', count: 70 }, { value: 'journal', label: 'Journal' }]} value="register" onChange={change} />);
    expect(screen.getByText('70')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: /Journal/ }));
    expect(change).toHaveBeenCalledWith('journal');
  });

  it('shows its label', () => {
    render(<StatusBadge tone="success">Approved</StatusBadge>);
    expect(screen.getByText('Approved')).toBeTruthy();
  });
});
