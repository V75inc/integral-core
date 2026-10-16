import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { EntryMetaFields } from '../EntryMetaFields';
import type { OperationalModelFieldSpec } from '../../../types';

vi.mock('../../../context/ScopeContext', () => ({
  useScope: () => ({ scope: { workspaceId: 'ws' } }),
}));
afterEach(cleanup);

function renderFields(
  fields: OperationalModelFieldSpec[],
  values: Record<string, unknown>,
  onCommitField?: (key: string, value: unknown) => Promise<void>
) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EntryMetaFields
        fields={fields}
        values={values}
        variant="detail"
        onCommitField={onCommitField}
      />
    </QueryClientProvider>
  );
}

it('clamps a long max_lines value and expands it with Read more', () => {
  const lines = Array.from({ length: 12 }, (_, i) => `[t] User: line ${i}`).join('\n');
  const fields = [
    { key: 'history', name: 'History', type: 'text', max_lines: 6 },
  ] as OperationalModelFieldSpec[];
  renderFields(fields, { history: lines });

  const value = screen.getByText(/line 0/);
  expect(value.parentElement?.getAttribute('style') || '').toContain(
    '-webkit-line-clamp: 6'
  );

  fireEvent.click(screen.getByRole('button', { name: 'Read more' }));
  expect(value.parentElement?.getAttribute('style') || '').not.toContain(
    '-webkit-line-clamp'
  );
  expect(screen.getByRole('button', { name: 'Show less' })).toBeTruthy();
});

it('shows no Read more for a short value', () => {
  const fields = [
    { key: 'history', name: 'History', type: 'text', max_lines: 6 },
  ] as OperationalModelFieldSpec[];
  renderFields(fields, { history: '[t] User: hi' });
  expect(screen.queryByRole('button', { name: 'Read more' })).toBeNull();
});

it('spans a full_width field across the grid', () => {
  const fields = [
    { key: 'summary', name: 'Summary', type: 'text', full_width: true },
  ] as OperationalModelFieldSpec[];
  renderFields(fields, { summary: 'a summary line' });

  const label = screen.getByText('Summary');
  expect(label.parentElement?.className).toContain('sm:col-span-2');
});

it('omits an empty hide_when_empty field on the detail', () => {
  const fields = [
    { key: 'owner', name: 'Owner', type: 'member', hide_when_empty: true },
    { key: 'phone', name: 'Phone', type: 'text' },
  ] as OperationalModelFieldSpec[];
  renderFields(fields, { phone: '555' });

  expect(screen.queryByText('Owner')).toBeNull();
  expect(screen.getByText('Phone')).toBeVisible();
});

it('renders an editable select in the detail and commits the choice', async () => {
  const onCommit = vi.fn().mockResolvedValue(undefined);
  const fields = [
    {
      key: 'pipeline_status',
      name: 'Pipeline status',
      type: 'select',
      enum: ['new', 'contacted'],
    },
  ] as OperationalModelFieldSpec[];
  renderFields(fields, { pipeline_status: 'new' }, onCommit);

  const combo = screen.getByRole('combobox', { name: /Pipeline status/ });
  fireEvent.focus(combo);
  fireEvent.click(screen.getByRole('option', { name: 'Contacted' }));
  await waitFor(() =>
    expect(onCommit).toHaveBeenCalledWith('pipeline_status', 'contacted')
  );
});

it('filters the inline select via its search box and commits', async () => {
  const onCommit = vi.fn().mockResolvedValue(undefined);
  const fields = [
    {
      key: 'pipeline_status',
      name: 'Pipeline status',
      type: 'select',
      enum: ['new', 'contacted'],
    },
  ] as OperationalModelFieldSpec[];
  renderFields(fields, { pipeline_status: 'new' }, onCommit);

  const combo = screen.getByRole('combobox', { name: /Pipeline status/ });
  fireEvent.focus(combo);
  fireEvent.change(combo, { target: { value: 'cont' } });

  expect(screen.queryByRole('option', { name: 'New' })).toBeNull();
  fireEvent.click(screen.getByRole('option', { name: 'Contacted' }));
  await waitFor(() =>
    expect(onCommit).toHaveBeenCalledWith('pipeline_status', 'contacted')
  );
});

it('renders a select as plain text for a read-only viewer', () => {
  const fields = [
    {
      key: 'pipeline_status',
      name: 'Pipeline status',
      type: 'select',
      enum: ['new', 'contacted'],
    },
  ] as OperationalModelFieldSpec[];
  renderFields(fields, { pipeline_status: 'new' });

  expect(screen.queryByRole('button', { name: /Pipeline status/ })).toBeNull();
  expect(screen.getByText('New')).toBeVisible();
});

it('shows group headers on the detail but not on cards', () => {
  const fields = [
    { key: 'organization', name: 'Organization', type: 'text', group: 'Contact' },
  ] as OperationalModelFieldSpec[];

  renderFields(fields, { organization: 'M&B' });
  expect(screen.getByText('Contact')).toBeTruthy();
  cleanup();

  render(
    <QueryClientProvider client={new QueryClient()}>
      <EntryMetaFields
        fields={fields}
        values={{ organization: 'M&B' }}
        variant="card"
      />
    </QueryClientProvider>
  );
  expect(screen.queryByText('Contact')).toBeNull();
});
