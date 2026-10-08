import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SeamlessField } from '../SeamlessField';
import type { OperationalModelFieldSpec } from '../../../types';

vi.mock('../relations', () => ({
  useRelationLabels: () => ({ targets: [{ label: 'Current opportunity' }] }),
  RelationValue: () => <a href="/entries/current">Current opportunity</a>,
}));
afterEach(cleanup);

const field: OperationalModelFieldSpec = {
  key: 'opportunity', name: 'Opportunity', type: 'relation',
  relation: { target: 'entry', many: false }, required: true,
};
const choices = [
  { value: 'current', label: 'Current opportunity' },
  { value: 'next', label: 'Next opportunity' },
];

describe('single relation display and editing', () => {
  it('shows one linked value and opens the selector only when editing', () => {
    const change = vi.fn();
    render(<SeamlessField field={field} value="current" onChange={change} relationChoices={choices} />);
    expect(screen.getAllByText('Current opportunity')).toHaveLength(1);
    expect(screen.getByRole('link')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Opportunity (required)' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Change Opportunity' }));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Opportunity (required)' }));
    fireEvent.click(screen.getByRole('option', { name: 'Next opportunity' }));
    expect(change).toHaveBeenCalledWith('next');
  });

  it('cancels editing without changing the relation', () => {
    const change = vi.fn();
    render(<SeamlessField field={field} value="current" onChange={change} relationChoices={choices} />);
    fireEvent.click(screen.getByRole('button', { name: 'Change Opportunity' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel editing Opportunity' }));
    expect(screen.getAllByText('Current opportunity')).toHaveLength(1);
    expect(screen.getByRole('link')).toBeVisible();
    expect(change).not.toHaveBeenCalled();
  });

  it('keeps read-only relations linked without an editor', () => {
    render(<SeamlessField field={{ ...field, readonly: true }} value="current" onChange={vi.fn()} relationChoices={choices} />);
    expect(screen.getByRole('link')).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
