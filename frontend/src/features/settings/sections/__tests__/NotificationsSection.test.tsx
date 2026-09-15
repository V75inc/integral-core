/**
 * Phase 9 Plan 09-04 — NotificationsSection toggle matrix (NOTIF-03).
 *
 * Three cases (locked in plan):
 *   1. Renders 5 NotificationKinds × 3 channels = 15 checkboxes, each
 *      with aria-label `${kind} via ${channel}`.
 *   2. Toggling a cell invokes the PATCH client with the expected
 *      partial body.
 *   3. When `whatsapp.opted_in_at === null`, the WhatsApp column is
 *      disabled AND a "verify phone" link is present.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock both the raw client fns AND the React Query hook wrappers.
// The component imports the hooks (`useNotificationPreferences` +
// `useUpdateNotificationPreferences`); replacing them with controlled
// implementations gives the test deterministic data + mutation capture
// without bridging through a QueryClient transport.
vi.mock('../../../../api/notificationPreferences', () => ({
  useNotificationPreferences: vi.fn(),
  useUpdateNotificationPreferences: vi.fn(),
  fetchNotificationPreferences: vi.fn(),
  patchNotificationPreferences: vi.fn(),
}));

import {
  fetchNotificationPreferences,
  patchNotificationPreferences,
  useNotificationPreferences,
  useUpdateNotificationPreferences,
} from '../../../../api/notificationPreferences';
import type { NotificationPreferences } from '../../../../api/notificationPreferences';
import { NotificationsSection } from '../NotificationsSection';

const mockedFetch = fetchNotificationPreferences as unknown as ReturnType<
  typeof vi.fn
>;
const mockedPatch = patchNotificationPreferences as unknown as ReturnType<
  typeof vi.fn
>;
const mockedUseQuery = useNotificationPreferences as unknown as ReturnType<
  typeof vi.fn
>;
const mockedUseMutation = useUpdateNotificationPreferences as unknown as ReturnType<
  typeof vi.fn
>;

function makePrefs(
  overrides: Partial<NotificationPreferences> = {},
): NotificationPreferences {
  const channelMatrix = { in_app: true, email: true, whatsapp: false };
  return {
    channels: { ...channelMatrix },
    kinds: {
      mention: { ...channelMatrix },
      share: { ...channelMatrix },
      agent_pending_write: { ...channelMatrix },
      invitation: { ...channelMatrix },
      system: { in_app: true, email: false, whatsapp: false },
    },
    whatsapp: { opted_in_at: null, phone_e164: null },
    ...overrides,
  };
}

function renderSection() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <NotificationsSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedPatch.mockImplementation(async (partial: Partial<NotificationPreferences>) => ({
    ...makePrefs(),
    ...partial,
  }));
  // Default hook returns — tests override via mockReturnValueOnce.
  mockedUseQuery.mockReturnValue({
    data: makePrefs(),
    isLoading: false,
    isError: false,
    error: null,
  });
  mockedUseMutation.mockReturnValue({
    mutate: mockedPatch,
    isPending: false,
  });
});

afterEach(() => {
  cleanup();
});

describe('<NotificationsSection />', () => {
  it('renders a 5×3 checkbox matrix with aria-labels', async () => {
    renderSection();
    expect(
      screen.getByRole('checkbox', { name: /mention via in_app/i }),
    ).toBeInTheDocument();
    const kinds = ['mention', 'share', 'agent_pending_write', 'invitation', 'system'];
    const channels = ['in_app', 'email', 'whatsapp'];
    for (const kind of kinds) {
      for (const channel of channels) {
        const box = screen.getByRole('checkbox', {
          name: new RegExp(`${kind} via ${channel}`, 'i'),
        });
        expect(box).toBeInTheDocument();
      }
    }
    // Total: 15 checkboxes for the matrix.
    const all = screen.getAllByRole('checkbox');
    expect(all.length).toBeGreaterThanOrEqual(15);
    expect(mockedFetch).toBeDefined();
  });

  it('toggling a cell PATCHes the API with the expected partial', async () => {
    renderSection();
    const cell = screen.getByRole('checkbox', {
      name: /share via email/i,
    });
    expect(cell).toBeChecked();
    fireEvent.click(cell);
    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledTimes(1);
    });
    const partial = mockedPatch.mock.calls[0][0] as Partial<NotificationPreferences>;
    // The body must scope the change to share.email = false; other cells
    // in the same kind must remain true so the deep-merge produces the
    // expected matrix.
    expect(partial.kinds?.share?.email).toBe(false);
    expect(partial.kinds?.share?.in_app).toBe(true);
  });

  it('disables WhatsApp column and shows verify-phone link when opted_in_at is null', async () => {
    renderSection();
    // All five whatsapp cells must be disabled when opted_in_at is null.
    expect(
      screen.getByRole('checkbox', { name: /mention via whatsapp/i }),
    ).toBeInTheDocument();
    const whatsappCells = screen
      .getAllByRole('checkbox')
      .filter(el => /via whatsapp/i.test(el.getAttribute('aria-label') ?? ''));
    expect(whatsappCells.length).toBeGreaterThanOrEqual(5);
    for (const cell of whatsappCells) {
      expect(cell).toBeDisabled();
    }
    // A "verify phone" link must be present.
    expect(
      screen.getByRole('link', { name: /verify phone/i }),
    ).toBeInTheDocument();
  });
});
