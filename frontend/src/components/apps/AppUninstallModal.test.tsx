/**
 * AppUninstallModal — uninstall flow tests.
 *
 * Phase 10 Plan 10-05 (APP-LIFECYCLE-01). Covers:
 *  - Normal uninstall → POST → onUninstalled + onClose.
 *  - 409 blocking-deps response → surfaces banner + force-button.
 *  - Force-uninstall → POST with ?force=true → onUninstalled.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  cleanup,
} from '@testing-library/react';
import { AppUninstallModal } from './AppUninstallModal';

vi.mock('../../api/client', () => ({
  default: {
    post: vi.fn(),
  },
}));

import apiClient from '../../api/client';

const mockedPost = apiClient.post as unknown as ReturnType<typeof vi.fn>;

describe('AppUninstallModal', () => {
  beforeEach(() => {
    mockedPost.mockReset();
  });
  afterEach(() => cleanup());

  it('uninstalls successfully when no dependents', async () => {
    const onUninstalled = vi.fn();
    const onClose = vi.fn();
    mockedPost.mockResolvedValueOnce({
      data: { status: 'uninstalled', app_id: 'app_1' },
    });
    render(
      <AppUninstallModal
        open
        onClose={onClose}
        appId="app_1"
        appName="Test App"
        onUninstalled={onUninstalled}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('uninstall-confirm'));
    });
    await waitFor(() => expect(onUninstalled).toHaveBeenCalledWith('app_1'));
    expect(onClose).toHaveBeenCalled();
    expect(mockedPost).toHaveBeenCalledWith('/apps/app_1/uninstall', {});
  });

  it('surfaces blocking dependents banner on 409 and reveals force button', async () => {
    mockedPost.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          error_code: 'app_uninstall_blocked',
          message: 'Uninstall blocked',
          details: {
            blocking_dependents: [
              {
                app_id: 'app_dependent',
                app_name: 'Dependent App',
                dep_key: 'test-app',
              },
            ],
          },
        },
      },
    });
    render(
      <AppUninstallModal
        open
        onClose={vi.fn()}
        appId="app_1"
        appName="Test App"
        onUninstalled={vi.fn()}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('uninstall-confirm'));
    });
    await waitFor(() =>
      expect(screen.getByTestId('blocking-deps-banner')).toBeInTheDocument(),
    );
    expect(screen.getByText('Dependent App')).toBeInTheDocument();
    expect(screen.getByTestId('uninstall-force')).toBeInTheDocument();
  });

  it('surfaces blocking_references banner with coalesced counts (Phase 10 Plan 10-06)', async () => {
    mockedPost.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          error_code: 'app_uninstall_blocked',
          message: 'Cross-App refs',
          details: {
            blocking_dependents: [],
            blocking_references: [
              {
                source_app_id: 'consumer',
                source_app_name: 'Consumer App',
                source_entry_id: 'e1',
                source_track_id: 't1',
                relation_field_key: 'employees',
              },
              {
                source_app_id: 'consumer',
                source_app_name: 'Consumer App',
                source_entry_id: 'e2',
                source_track_id: 't1',
                relation_field_key: 'employees',
              },
              {
                source_app_id: 'downstream',
                source_app_name: 'Downstream App',
                source_entry_id: 'e3',
                source_track_id: 't2',
                relation_field_key: 'subject',
              },
            ],
          },
        },
      },
    });
    render(
      <AppUninstallModal
        open
        onClose={vi.fn()}
        appId="provider_app"
        appName="Provider App"
        onUninstalled={vi.fn()}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('uninstall-confirm'));
    });
    await waitFor(() =>
      expect(screen.getByTestId('blocking-refs-banner')).toBeInTheDocument(),
    );
    // Coalesced summary: consumer app (2 refs) + downstream app (1 ref).
    expect(screen.getByText('Consumer App')).toBeInTheDocument();
    expect(screen.getByText(/2 references/)).toBeInTheDocument();
    expect(screen.getByText('Downstream App')).toBeInTheDocument();
    expect(screen.getByText(/1 reference/)).toBeInTheDocument();
    expect(screen.getByTestId('uninstall-force')).toBeInTheDocument();
  });

  it('force-uninstall posts with ?force=true and emits force_uninstalled', async () => {
    const onUninstalled = vi.fn();
    mockedPost
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: {
            error_code: 'app_uninstall_blocked',
            message: 'Blocked',
            details: {
              blocking_dependents: [
                { app_id: 'b', app_name: 'B', dep_key: 'k' },
              ],
            },
          },
        },
      })
      .mockResolvedValueOnce({
        data: { status: 'force_uninstalled', app_id: 'app_1' },
      });
    render(
      <AppUninstallModal
        open
        onClose={vi.fn()}
        appId="app_1"
        appName="Test"
        onUninstalled={onUninstalled}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('uninstall-confirm'));
    });
    await waitFor(() =>
      expect(screen.getByTestId('uninstall-force')).toBeInTheDocument(),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('uninstall-force'));
    });
    await waitFor(() => expect(onUninstalled).toHaveBeenCalledWith('app_1'));
    expect(mockedPost).toHaveBeenLastCalledWith('/apps/app_1/uninstall?force=true', {});
  });
});
