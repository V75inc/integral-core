import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import type { ReactNode } from 'react';
import fixture from '../../../fixtures/a09QueryProjectionFixture.json';
import type { Entry, OperationalModelFieldSpec, SavedView, Track } from '../../../types';
import { ConfirmProvider } from '../../../context/ConfirmContext';
import { ToastProvider } from '../../../context/ToastContext';
import { KanbanWidget } from '../KanbanWidget';

const invokeOperation = vi.hoisted(() => vi.fn());

vi.mock('../../../api/extensions', () => ({
  extensionsApi: {
    invokeOperation: (...args: unknown[]) => invokeOperation(...args),
  },
}));

type DndHandlers = {
  onDragStart?: (event: { active: { id: string } }) => void;
  onDragOver?: (event: { active: { id: string }; over: { id: string } | null }) => void;
  onDragEnd?: (event: { active: { id: string }; over: { id: string } | null }) => void;
};

const dndState = vi.hoisted(() => {
  const handlers: DndHandlers = {};
  (globalThis as { __kanbanDnd?: DndHandlers }).__kanbanDnd = handlers;
  return handlers;
});

function dndHandlers(): DndHandlers {
  return (globalThis as { __kanbanDnd?: DndHandlers }).__kanbanDnd || dndState;
}

async function simulateDrop(entryId: string, targetCol: string) {
  const h = dndHandlers();
  await act(async () => {
    h.onDragStart?.({ active: { id: entryId } });
  });
  await act(async () => {
    h.onDragOver?.({
      active: { id: entryId },
      over: { id: targetCol },
    });
  });
  await act(async () => {
    h.onDragEnd?.({
      active: { id: entryId },
      over: { id: targetCol },
    });
  });
}

const stableRelationResult = vi.hoisted(() => ({
  targets: [],
  loading: false,
  error: null,
}));
const stableMemberLabels = vi.hoisted(() => ({
  resolveMemberLabel: () => undefined,
  loading: false,
}));

vi.mock('@dnd-kit/core', () => {
  const PassThrough = ({
    children,
    onDragStart,
    onDragOver,
    onDragEnd,
  }: {
    children?: ReactNode;
    onDragStart?: DndHandlers['onDragStart'];
    onDragOver?: DndHandlers['onDragOver'];
    onDragEnd?: DndHandlers['onDragEnd'];
  }) => {
    const slot = (globalThis as { __kanbanDnd?: DndHandlers }).__kanbanDnd;
    if (slot) {
      slot.onDragStart = onDragStart;
      slot.onDragOver = onDragOver;
      slot.onDragEnd = onDragEnd;
    }
    return <>{children}</>;
  };
  return {
    DndContext: PassThrough,
    DragOverlay: ({ children }: { children?: ReactNode }) => <>{children}</>,

    KeyboardSensor: class {},
    MouseSensor: class {},
    TouchSensor: class {},
    useDroppable: () => ({ setNodeRef: () => {}, isOver: false }),
    useSensor: () => ({}),
    useSensors: () => [],
    closestCenter: () => [],
    pointerWithin: () => [],
    rectIntersection: () => [],
    getFirstCollision: () => undefined,
  };
});

vi.mock('@dnd-kit/sortable', () => {
  const PassThrough = ({ children }: { children?: ReactNode }) => <>{children}</>;
  return {
    SortableContext: PassThrough,
    verticalListSortingStrategy: () => null,
    horizontalListSortingStrategy: () => null,
    useSortable: () => ({
      attributes: {},
      listeners: {},
      setNodeRef: () => {},
      transform: null,
      transition: undefined,
      isDragging: false,
    }),
    sortableKeyboardCoordinates: () => null,
    arrayMove: <T,>(items: T[], from: number, to: number) => {
      const next = [...items];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next;
    },
  };
});

vi.mock('@dnd-kit/utilities', () => ({
  CSS: { Transform: { toString: () => undefined } },
}));

vi.mock('../../entries/relations', () => ({
  useRelationLabels: () => stableRelationResult,
}));

vi.mock('../../entries/members/useWorkspaceMemberLabelMap', () => ({
  useWorkspaceMemberLabelMap: () => stableMemberLabels,
}));

const fields: OperationalModelFieldSpec[] = [
  {
    key: 'status',
    name: 'Status',
    type: 'select',
    enum: ['draft', 'open', 'partially_paid', 'overdue', 'paid', 'void'],
  },
];

const openInvoice: Entry = {
  id: 'inv-1',
  title: 'INV-0001',
  track_id: 'track-invoices',
  type: 'Entry',
  author_id: 'user-1',
  created_at: '2026-10-01T00:00:00Z',
  custom_fields: {
    status: 'open',
    balance: 250,
    currency: 'GYD',
    customer: 'cust-1',
  },
};

const invoiceView: SavedView = {
  id: 'v-invoices',
  name: 'Pipeline',
  type: 'kanban',
  track_id: 'track-invoices',
  config: {
    group_by: 'custom_fields.status',
    kanban_columns: [
      { key: 'draft', label: 'Draft' },
      {
        key: 'open',
        label: 'Open',
        accepts_from: ['draft'],
        on_drop: {
          kind: 'operation',
          operation: 'document_action',
          payload: { action: 'issue' },
        },
      },
      { key: 'partially_paid', label: 'Partially paid', drop_target: false },
      { key: 'overdue', label: 'Overdue', drop_target: false },
      {
        key: 'paid',
        label: 'Paid',
        accepts_from: ['open', 'partially_paid', 'overdue'],
        on_drop: { kind: 'receive_payment' },
      },
      { key: 'void', label: 'Void', drop_target: false },
    ],
  },
};

const track: Track = {
  id: 'track-invoices',
  title: 'Invoices',
  visibility: 'private',
  owner_id: 'user-1',
  app: { id: 'app-finance', name: 'Finance' },
};

function Providers({ children }: { children: ReactNode }) {
  return (
    <ConfirmProvider>
      <ToastProvider>{children}</ToastProvider>
    </ConfirmProvider>
  );
}

describe('KanbanWidget invoice transitions', () => {
  beforeEach(() => {
    invokeOperation.mockReset();
    const h = dndHandlers();
    h.onDragStart = undefined;
    h.onDragOver = undefined;
    h.onDragEnd = undefined;
    // Keep fixture reference so the file mirrors A09 import graph.
    expect(fixture.entries.length).toBeGreaterThan(0);
  });

  afterEach(() => {
    cleanup();
  });

  it('opens the receive-payment sheet on drop to Paid and does not persist status', async () => {
    const commitEntry = vi.fn();

    render(
      <Providers>
        <KanbanWidget
          entries={[openInvoice]}
          view={invoiceView}
          track={track}
          isLoading={false}
          isEditor
          onEntryOpen={() => {}}
          onEntryPersist={commitEntry}
          fields={fields}
        />
      </Providers>
    );

    expect(dndHandlers().onDragStart).toBeTypeOf('function');
    expect(dndHandlers().onDragEnd).toBeTypeOf('function');

    await simulateDrop('inv-1', 'paid');

    // Transition path must not PATCH status=paid (sheet records a payment instead).
    const paidWrites = commitEntry.mock.calls.filter(call => {
      const entry = call[0] as Entry;
      return entry?.custom_fields?.status === 'paid';
    });
    expect(paidWrites).toHaveLength(0);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Receive payment' })).toBeInTheDocument();
    });
  });

  it('rejects drop onto Void without writing status', async () => {
    const commitEntry = vi.fn();

    render(
      <Providers>
        <KanbanWidget
          entries={[openInvoice]}
          view={invoiceView}
          track={track}
          isLoading={false}
          isEditor
          onEntryOpen={() => {}}
          onEntryPersist={commitEntry}
          fields={fields}
        />
      </Providers>
    );

    await simulateDrop('inv-1', 'void');

    await waitFor(() => {
      expect(
        screen.getByText(/Void an invoice from its detail view/i)
      ).toBeInTheDocument();
    });
    expect(commitEntry).not.toHaveBeenCalled();
  });
});
