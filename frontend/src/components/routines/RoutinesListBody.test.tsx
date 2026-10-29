import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfirmProvider } from '../../context/ConfirmContext';
import type { RoutineResponse } from '../../api/routines';
import { RoutinesListBody } from './RoutinesListBody';

vi.mock('../../api/routines', () => ({
  cancelRoutine: vi.fn(), deleteRoutine: vi.fn(), updateRoutine: vi.fn(),
  getRoutineActivity: vi.fn(),
}));
import { deleteRoutine } from '../../api/routines';

afterEach(() => { cleanup(); vi.resetAllMocks(); });

function routine(id: string, status: RoutineResponse['status']): RoutineResponse {
  return {
    id, instruction: id, status, cron: '0 8 * * *', timezone: 'UTC',
    write_scope: [], next_run_at: null, last_run_at: null, last_run_status: null,
    last_run_error: null, consecutive_failures: 0, max_runs: null, run_count: 0,
    thread_id: 'thread', workspace_id: 'workspace', created_at: null,
    updated_at: null, running: false,
  };
}

describe('background task cleanup', () => {
  it('confirms removal of terminal tasks, excludes active tasks and reports partial failures', async () => {
    const onChanged = vi.fn();
    vi.mocked(deleteRoutine).mockResolvedValueOnce({ id: 'done', deleted: true });
    vi.mocked(deleteRoutine).mockRejectedValueOnce(new Error('offline'));
    render(<MemoryRouter><ConfirmProvider><RoutinesListBody
      rows={[routine('done', 'completed'), routine('cancelled', 'cancelled'), routine('active', 'active')]}
      loading={false} error={null} onRetry={vi.fn()} onChanged={onChanged}
    /></ConfirmProvider></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Select all completed and cancelled' }));
    expect(screen.queryByLabelText('Select active')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove selected (2)' }));
    expect(deleteRoutine).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove tasks' }));
    await waitFor(() => expect(deleteRoutine).toHaveBeenCalledTimes(2));
    expect(deleteRoutine).toHaveBeenNthCalledWith(1, 'done');
    expect(deleteRoutine).toHaveBeenNthCalledWith(2, 'cancelled');
    expect(await screen.findByRole('alert')).toHaveTextContent('1 task could not be removed');
    expect(screen.getByRole('button', { name: 'Remove selected (1)' })).toBeEnabled();
    expect(onChanged).toHaveBeenCalledOnce();
  });
});
