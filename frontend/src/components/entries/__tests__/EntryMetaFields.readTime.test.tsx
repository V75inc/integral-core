import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { EntryMetaFields } from '../EntryMetaFields';

vi.mock('../../../context/ScopeContext', () => ({ useScope: () => ({ scope: { workspaceId: 'ws' } }) }));
afterEach(cleanup);

it('shows a projected value without offering an inline write', () => {
  const commit = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}>
    <EntryMetaFields fields={[{ key: 'score', name: 'Score', type: 'number' }]}
      values={{ score: 7 }} variant="detail" onCommitField={commit} readOnlyKeys={['score']} />
  </QueryClientProvider>);
  expect(screen.getByText('7')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Score' })).toBeDisabled();
  expect(commit).not.toHaveBeenCalled();
});
