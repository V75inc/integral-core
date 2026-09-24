/**
 * AppManagerDialog — install-only Apply; per-row Uninstall.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ComponentProps } from 'react';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  cleanup,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppManagerDialog } from './AppManagerDialog';
import type { App, OperationalModelNode } from '../../types';

const mockListProfiles = vi.fn();
const mockBatchInstall = vi.fn();
const mockUninstall = vi.fn();
const mockUninstallPreflight = vi.fn();
const mockGetWorkItem = vi.fn();

vi.mock('../../api/operationalModels', () => ({
  operationalModelsApi: {
    list: (...args: unknown[]) => mockListProfiles(...args),
  },
}));

vi.mock('../../api/apps', () => ({
  appsApi: {
    batchInstall: (...args: unknown[]) => mockBatchInstall(...args),
    uninstall: (...args: unknown[]) => mockUninstall(...args),
    uninstallPreflight: (...args: unknown[]) => mockUninstallPreflight(...args),
    finalizeInstall: vi.fn(),
    getAppSettings: vi.fn(),
  },
}));

vi.mock('../../api/workItems', () => ({
  workItemsApi: {
    get: (...args: unknown[]) => mockGetWorkItem(...args),
  },
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1', user_id: 'user-1' },
  }),
}));

const INSTALLED_APP: App = {
  id: 'app-installed',
  name: 'Content Factory',
  owner_user_id: 'user-1',
  installed_from_library_id: 'lib-cf',
  source_operational_model_slug: 'content-factory',
  lifecycle_state: 'active',
};

const LIB_INSTALLED: OperationalModelNode = {
  id: 'lib-cf',
  name: 'Content Factory',
  library_package: true,
  manifest: {
    scope: 'app',
    package: { name: 'Content Factory', slug: 'content-factory', description: 'Installed pkg' },
    app: { tracks: [] },
  },
};

const LIB_AVAILABLE: OperationalModelNode = {
  id: 'lib-hr',
  name: 'HR Suite',
  library_package: true,
  manifest: {
    scope: 'app',
    package: { name: 'HR Suite', slug: 'hr-suite', description: 'HR bundle' },
    app: { tracks: [{ key: 'employees', name: 'Employees' }] },
  },
};

function renderDialog(
  props: Partial<ComponentProps<typeof AppManagerDialog>> = {},
) {
  return render(
    <MemoryRouter>
      <AppManagerDialog
        isOpen
        onClose={vi.fn()}
        apps={[INSTALLED_APP]}
        onChanged={vi.fn()}
        onCreateBlankApp={vi.fn()}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe('AppManagerDialog', () => {
  beforeEach(() => {
    mockListProfiles.mockReset();
    mockBatchInstall.mockReset();
    mockUninstall.mockReset();
    mockUninstallPreflight.mockReset();
    mockGetWorkItem.mockReset();
    mockUninstallPreflight.mockResolvedValue({
      app_id: 'app-installed',
      can_uninstall: true,
      blocking_dependents: [],
      blocking_references: [],
      entry_count: 0,
      requires_data_confirmation: false,
    });
    mockGetWorkItem.mockResolvedValue({
      work_item_id: 'work_1',
      kind: 'app_lifecycle',
      status: 'running',
      workspace_id: 'ws_1',
      app_id: 'app-installed',
      attempt: 1,
      next_attempt_at: '',
      updated_at: '',
      result_refs: [],
    });
    mockListProfiles.mockResolvedValue([LIB_INSTALLED, LIB_AVAILABLE]);
  });
  afterEach(() => cleanup());

  it('renders installed and available sections after load', async () => {
    renderDialog();
    await waitFor(() =>
      expect(screen.getByTestId('app-manager-installed')).toBeInTheDocument(),
    );
    const installed = screen.getByTestId('app-manager-installed');
    expect(installed).toHaveTextContent('Content Factory');
    expect(
      await screen.findByTestId('app-manager-row-hr-suite'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('app-manager-available')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Installed' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Available' })).toBeInTheDocument();
  });

  it('marks already-installed packages and disables their row', async () => {
    renderDialog();
    await waitFor(() =>
      expect(screen.getByTestId('app-manager-row-content-factory')).toBeInTheDocument(),
    );
    const installedRow = screen.getByTestId('app-manager-row-content-factory');
    expect(installedRow).toHaveTextContent('Installed');
    const rowButton = installedRow.querySelector('button');
    expect(rowButton).toBeDisabled();
  });

  it('enables install when an available package is selected', async () => {
    renderDialog();
    await waitFor(() =>
      expect(screen.getByTestId('app-manager-apply')).toBeInTheDocument(),
    );
    const apply = screen.getByTestId('app-manager-apply');
    expect(apply).toBeDisabled();

    const hrRow = (
      await screen.findByTestId('app-manager-row-hr-suite')
    ).querySelector('button');
    await act(async () => {
      fireEvent.click(hrRow!);
    });
    await waitFor(() => expect(apply).not.toBeDisabled());
    expect(apply).toHaveTextContent('Install selected (1)');
  });

  it('Apply installs only — does not call uninstall', async () => {
    const onChanged = vi.fn();
    mockBatchInstall.mockResolvedValue({
      installed: [
        {
          library_cp_id: 'lib-hr',
          app_id: 'app-new',
          status: 'active',
          name: 'HR Suite',
        },
      ],
      skipped: [],
      failed: [],
    });

    renderDialog({ onChanged });
    await waitFor(() =>
      expect(screen.getByTestId('app-manager-installed')).toBeInTheDocument(),
    );

    expect(
      screen.getByTestId('app-manager-uninstall-app-installed'),
    ).toBeInTheDocument();

    const hrRow = (
      await screen.findByTestId('app-manager-row-hr-suite')
    ).querySelector('button');
    await act(async () => {
      fireEvent.click(hrRow!);
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId('app-manager-apply'));
    });

    await waitFor(() => expect(mockBatchInstall).toHaveBeenCalled());
    expect(mockUninstall).not.toHaveBeenCalled();
    expect(mockBatchInstall).toHaveBeenCalledWith(
      [{ library_cp_id: 'lib-hr' }],
      { include_seed_data: true },
    );
    await waitFor(() =>
      expect(screen.getByTestId('app-manager-result')).toBeInTheDocument(),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it('opens uninstall modal from Uninstall control', async () => {
    renderDialog();
    await waitFor(() =>
      expect(
        screen.getByTestId('app-manager-uninstall-app-installed'),
      ).toBeInTheDocument(),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('app-manager-uninstall-app-installed'));
    });
    await waitFor(() =>
      expect(screen.getByTestId('uninstall-confirm')).toBeInTheDocument(),
    );
  });

  it('disables Uninstall when preflight reports dependents', async () => {
    mockUninstallPreflight.mockResolvedValue({
      app_id: 'app-installed',
      can_uninstall: false,
      blocking_dependents: [
        { app_id: 'dep-1', app_name: 'Payroll', dep_key: 'content-factory' },
      ],
      blocking_references: [],
      entry_count: 0,
      requires_data_confirmation: false,
    });
    renderDialog();
    await waitFor(() =>
      expect(
        screen.getByTestId('app-manager-uninstall-app-installed'),
      ).toBeDisabled(),
    );
    expect(
      screen.getByTestId('app-manager-uninstall-app-installed'),
    ).toHaveAttribute('title', expect.stringContaining('Payroll'));
  });

  it('transitions to settings finalize when batch returns awaiting_settings', async () => {
    mockBatchInstall.mockResolvedValue({
      installed: [
        {
          library_cp_id: 'lib-hr',
          app_id: 'app-pending',
          status: 'awaiting_settings',
          name: 'HR Suite',
          install_token: 'tok_1',
          settings_schema: {
            type: 'object',
            properties: {
              region: { type: 'string', title: 'Region' },
            },
          },
        },
      ],
      skipped: [],
      failed: [],
    });

    renderDialog();
    await waitFor(() =>
      expect(screen.getByTestId('app-manager-row-hr-suite')).toBeInTheDocument(),
    );

    const hrRow = (
      await screen.findByTestId('app-manager-row-hr-suite')
    ).querySelector('button');
    await act(async () => {
      fireEvent.click(hrRow!);
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('app-manager-apply'));
    });

    await waitFor(() =>
      expect(screen.getByTestId('app-settings-form')).toBeInTheDocument(),
    );
    expect(screen.getByText('Region')).toBeInTheDocument();
    expect(screen.getByText('Finish app setup')).toBeInTheDocument();
  });

  it('shows Needs settings badge for awaiting_settings apps', async () => {
    renderDialog({
      apps: [
        {
          ...INSTALLED_APP,
          lifecycle_state: 'awaiting_settings',
        },
      ],
    });
    await waitFor(() =>
      expect(screen.getByText('Needs settings')).toBeInTheDocument(),
    );
  });
});
