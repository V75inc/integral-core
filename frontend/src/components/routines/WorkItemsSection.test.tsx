import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { WorkItemsSection } from './WorkItemsSection';
const list = vi.fn();
vi.mock('../../api/workItems', () => ({ workItemsApi: { list: (...args: unknown[]) => list(...args) } }));
afterEach(() => { cleanup(); list.mockReset(); });
const item = { work_item_id: 'update-1', kind: 'app_lifecycle', status: 'succeeded', workspace_id: 'workspace-1', app_id: 'app-1', attempt: 1, updated_at: new Date().toISOString() };
describe('Workspace work', () => {
  it('shows terminal lifecycle state and loads older results', async () => {
    list.mockResolvedValueOnce({ items: [item], next_cursor: 'next' }).mockResolvedValueOnce({ items: [{ ...item, work_item_id: 'failed-1', status: 'failed', failure: { code: 'error', message: 'Update failed' } }], next_cursor: null });
    render(<WorkItemsSection workspaceId="workspace-1" />);
    await screen.findByRole('heading', { name: 'App lifecycle · Completed', level: 3 });
    expect(screen.getByText('Work ID').tagName).toBe('DT');
    expect(screen.getByText('update-1').tagName).toBe('DD');
    expect(screen.getByText('App ID').tagName).toBe('DT');
    expect(screen.getByText('app-1').tagName).toBe('DD');
    expect(screen.getByText('update-1')).toHaveClass('truncate');
    expect(screen.getByText('update-1')).toHaveAttribute('title', 'update-1');
    expect(list).toHaveBeenCalledWith('workspace-1', undefined);
    fireEvent.click(screen.getByText('Load older work'));
    await screen.findByText('Update failed');
    expect(list).toHaveBeenCalledWith('workspace-1', 'next');
  });
  it('shows failed observation and allows retry', async () => {
    list.mockRejectedValueOnce(new Error('Status unavailable')).mockResolvedValueOnce({ items: [], next_cursor: null });
    render(<WorkItemsSection workspaceId="workspace-1" />);
    await screen.findByRole('alert');
    fireEvent.click(screen.getByText('Refresh work'));
    await screen.findByText('No workspace work yet.');
  });
  it('ignores results arriving after the scoped component unmounts', async () => {
    let resolve!: (value: unknown) => void;
    list.mockImplementationOnce(() => new Promise(r => { resolve = r; })).mockResolvedValueOnce({ items: [], next_cursor: null });
    const view = render(<WorkItemsSection key="one" workspaceId="workspace-1" />);
    view.rerender(<WorkItemsSection key="two" workspaceId="workspace-2" />);
    await screen.findByText('No workspace work yet.');
    resolve({ items: [item], next_cursor: null });
    await waitFor(() => expect(screen.queryByText('update-1')).not.toBeInTheDocument());
  });
});
