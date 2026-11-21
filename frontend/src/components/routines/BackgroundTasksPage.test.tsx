import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { BackgroundTasksPage } from './BackgroundTasksPage';

vi.mock('../../context/CrumbsContext', () => ({ useSetCrumbs: () => undefined }));
vi.mock('../../context/ScopeContext', () => ({ useScopeOptional: () => null }));
const { list, get } = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn() }));
vi.mock('../../api/routines', () => ({ listRoutines: list, getRoutine: get }));
beforeEach(() => { list.mockReset().mockResolvedValue({ routines: [] }); get.mockReset(); });
vi.mock('./RoutinesListBody', () => ({ RoutinesListBody: ({ rows, selectedRoutineId, emptyMessage }: { rows: Array<{ id: string; instruction: string }>; selectedRoutineId?: string; emptyMessage?: string }) => <div data-testid="routine-list" data-selected={selectedRoutineId}>{rows.map(row => <p key={row.id}>{row.instruction}</p>)}{!rows.length && emptyMessage}</div> }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Background Tasks navigation', () => {
  it('resolves the scheduled-routines link on client navigation', async () => {
    const scroll = vi.fn();
    const original = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
    Object.defineProperty(Element.prototype, 'scrollIntoView', { value: scroll, configurable: true });
    try {
      render(<MemoryRouter initialEntries={['/background-tasks#scheduled-routines']}><BackgroundTasksPage /></MemoryRouter>);
      const section = await screen.findByRole('region', { name: 'Scheduled routines' });
      expect(section).toHaveAttribute('id', 'scheduled-routines');
      expect(scroll).toHaveBeenCalledWith({ block: 'start' });
    } finally {
      if (original) Object.defineProperty(Element.prototype, 'scrollIntoView', original);
      else Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
    }
  });
});


it('fetches an exact linked routine even when cancelled routines are excluded from the list', async () => {
  get.mockResolvedValue({ id: 'cancelled-one', instruction: 'Previously cancelled task', status: 'cancelled' });
  render(<MemoryRouter initialEntries={['/background-tasks?routine=cancelled-one#scheduled-routines']}><BackgroundTasksPage /></MemoryRouter>);
  expect(await screen.findByText('Previously cancelled task')).toBeInTheDocument();
  expect(get).toHaveBeenCalledWith('cancelled-one');
  expect(screen.getByTestId('routine-list')).toHaveAttribute('data-selected', 'cancelled-one');
});

it('keeps the routine list usable when a linked routine is deleted or inaccessible', async () => {
  get.mockRejectedValue(new Error('Not found'));
  render(<MemoryRouter initialEntries={['/background-tasks?routine=missing']}><BackgroundTasksPage /></MemoryRouter>);
  expect(await screen.findByText('This routine is unavailable or you no longer have access to it.')).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Scheduled routines' })).toBeInTheDocument();
});


it('reports loading and errors without a misleading zero count and keeps the filter with the routines', async () => {
  let reject!: (failure: Error) => void;
  list.mockImplementationOnce(() => new Promise((_, rejectResponse) => { reject = rejectResponse; }));
  render(<MemoryRouter><BackgroundTasksPage /></MemoryRouter>);
  const section = within(screen.getByRole('region', { name: 'Scheduled routines' }));
  expect(section.getByRole('combobox', { name: 'Routine status' })).toBeInTheDocument();
  expect(section.getByRole('status')).toHaveTextContent('Loading scheduled routines');
  expect(screen.queryByText('No active routines')).not.toBeInTheDocument();
  reject(new Error('Routine status unavailable'));
  expect(await section.findByRole('button', { name: 'Refresh scheduled routines' })).toBeInTheDocument();
  expect(screen.queryByText('0 scheduled routines')).not.toBeInTheDocument();
  fireEvent.change(section.getByRole('combobox', { name: 'Routine status' }), { target: { value: 'paused' } });
  expect(await section.findByText('No routines match this status.')).toBeInTheDocument();
  expect(list).toHaveBeenLastCalledWith({ status: 'paused' });
});
