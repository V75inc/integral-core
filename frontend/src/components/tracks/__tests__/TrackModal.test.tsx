import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { TrackModal } from '../TrackModal';
import { ConfirmProvider } from '../../../context/ConfirmContext';
import type { Track } from '../../../types';

const mocks = vi.hoisted(() => ({
  remove: vi.fn(), create: vi.fn(), update: vi.fn(), list: vi.fn(), apps: vi.fn(), getApp: vi.fn(),
  getModel: vi.fn(), templates: vi.fn(), preview: vi.fn(), publish: vi.fn(), toast: vi.fn(),
}));
vi.mock('../../../api', () => ({
  tracksApi: { create: mocks.create, update: mocks.update },
  appsApi: { list: mocks.apps, get: mocks.getApp, getOperationalModel: mocks.getModel, listTrackTemplates: mocks.templates },
  operationalModelsApi: { list: mocks.list, delete: mocks.remove },
}));
vi.mock('../../../api/operationalModels', () => ({ operationalModelsApi: {
  list: mocks.list, importPreview: mocks.preview, importPublish: mocks.publish,
} }));
vi.mock('../../../context/ScopeContext', () => ({ useScope: () => ({
  scope: { workspaceId: 'ws-qa' }, activeWorkspace: { id: 'ws-qa', name: 'QA Workspace' },
}) }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ showToast: mocks.toast }) }));

const model = {
  id: 'model-one', name: 'Reading List', description: 'Books and reading progress.', library_package: true,
  manifest: { scope: 'track', track: { entry_types: [{ key: 'book', name: 'Book', fields: [] }], views: [] } },
};
const imported = { ...model, id: 'imported-model', name: 'Imported Reading List' };
function show(props: Partial<React.ComponentProps<typeof TrackModal>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}>
    <ConfirmProvider><TrackModal open onClose={vi.fn()} onCreated={vi.fn()} {...props} /></ConfirmProvider>
  </QueryClientProvider></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.remove.mockResolvedValue(undefined);
  mocks.list.mockResolvedValue([model]);
  mocks.apps.mockResolvedValue([]);
  mocks.getApp.mockResolvedValue({ id: 'app-one', name: 'App One' });
  mocks.getModel.mockResolvedValue(null);
  mocks.templates.mockResolvedValue([]);
  mocks.create.mockResolvedValue({ id: 'track-new', title: 'Reading List' });
  mocks.update.mockResolvedValue({ id: 'track-edit', title: 'Edited' });
  mocks.preview.mockResolvedValue({ archive_type: 'single', packages: [{ package_name: 'Imported Reading List', scope_kind: 'track', entry_type_count: 1, view_count: 1, tag_count: 0, validation_errors: [] }] });
  mocks.publish.mockResolvedValue({ operational_model: imported });
});

it('offers blank, library and YAML controls, and creates a standalone blank Track only after confirmation', async () => {
  show();
  expect(screen.getByRole('heading', { name: 'Manage Tracks' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Import YAML' })).toBeInTheDocument();
  await screen.findByRole('button', { name: /Reading List/ });
  fireEvent.click(screen.getByRole('button', { name: 'Create your own track' }));
  fireEvent.change(screen.getByLabelText(/Track Name/), { target: { value: 'My standalone track' } });
  expect(mocks.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Create track' }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({ title: 'My standalone track', purpose: '', workspace_id: 'ws-qa', visibility: 'private' }));
});

it('uses only visible Track library models and leaves App assignment optional', async () => {
  mocks.list.mockResolvedValue([model,
    { ...model, id: 'other', name: 'Other workspace model', workspace_id: 'other-ws' },
    { ...model, id: 'app', name: 'App bundle', manifest: { scope: 'app', app: {} } },
  ]);
  show();
  fireEvent.click(await screen.findByRole('button', { name: /Reading List/ }));
  expect(screen.queryByRole('button', { name: /Other workspace model|App bundle/ })).toBeNull();
  expect(within(screen.getByLabelText('Track starting point')).getByText('Reading List')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(screen.getByLabelText(/Track Name/)).toHaveValue('Reading List');
  fireEvent.click(screen.getByRole('button', { name: 'Create track' }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ library_operational_model_id: 'model-one', workspace_id: 'ws-qa' })));
  expect(mocks.create.mock.calls[0][0]).not.toHaveProperty('app_id');
});

it('previews and imports .yml, selects the new model, and waits for Track confirmation', async () => {
  mocks.list.mockResolvedValueOnce([model]).mockResolvedValue([model, imported]);
  show();
  await screen.findByRole('button', { name: /Reading List/ });
  fireEvent.click(screen.getByRole('button', { name: 'Import YAML' }));
  const file = new File(['scope: track'], 'reading.yml', { type: 'application/yaml' });
  fireEvent.change(screen.getByLabelText('Template file'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Import' }));
  await waitFor(() => expect(mocks.publish).toHaveBeenCalledWith(file, 'ws-qa'));
  await waitFor(() => expect(within(screen.getByLabelText('Track starting point')).getByText('Imported Reading List')).toBeInTheDocument());
  expect(mocks.create).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  fireEvent.click(screen.getByRole('button', { name: 'Create track' }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ library_operational_model_id: 'imported-model' })));
});

it('blocks App-scoped uploads before publishing into the Track flow', async () => {
  mocks.preview.mockResolvedValue({ archive_type: 'single', packages: [{ package_name: 'App bundle', scope_kind: 'app', entry_type_count: 0, view_count: 0, tag_count: 0, validation_errors: [] }] });
  show();
  fireEvent.click(screen.getByRole('button', { name: 'Import YAML' }));
  fireEvent.change(screen.getByLabelText('Template file'), { target: { files: [new File(['scope: app'], 'app.yml')] } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  expect(await screen.findByText(/must contain a Track template/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
  expect(mocks.publish).not.toHaveBeenCalled();
});

it('preserves the existing edit dialog', async () => {
  show({ editTrack: { id: 'track-edit', title: 'Edited', visibility: 'private' } as Track });
  expect(screen.getByRole('heading', { name: 'Edit Track' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
  await waitFor(() => expect(mocks.update).toHaveBeenCalledWith('track-edit', expect.objectContaining({ title: 'Edited' })));
  expect(mocks.create).not.toHaveBeenCalled();
});

it('does not store selection help as the purpose of a model without a description', async () => {
  mocks.list.mockResolvedValue([{ ...model, description: '' }]);
  show();
  fireEvent.click(await screen.findByRole('button', { name: /Reading List/ }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(screen.getByLabelText('Purpose / Description')).toHaveValue('');
});

it('preserves App-scoped creation and its parent assignment', async () => {
  show({ appId: 'app-one' });
  await screen.findByRole('button', { name: /App defaults/ });
  expect(screen.queryByRole('button', { name: 'Import YAML' })).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  fireEvent.change(screen.getByLabelText(/Track Name/), { target: { value: 'App track' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create track' }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ app_id: 'app-one', title: 'App track' })));
});


it('confirms deletion of an uploaded model, removes it, and clears its selection without deleting Tracks', async () => {
  mocks.list.mockResolvedValue([{ ...model, workspace_id: 'ws-qa' }]);
  show();
  fireEvent.click(await screen.findByRole('button', { name: /Reading List Books/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete Reading List template' }));
  expect(await screen.findByRole('heading', { name: 'Delete Track template?' })).toBeInTheDocument();
  expect(screen.getByText(/Permanently delete “Reading List”/)).toHaveTextContent('Existing Tracks, Entries, and attachments');
  expect(screen.getByText(/Permanently delete “Reading List”/)).toHaveTextContent('cannot be undone');
  expect(mocks.remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Delete template' }));
  await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith('model-one'));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Delete Reading List template' })).toBeNull());
  expect(within(screen.getByLabelText('Track starting point')).getByText('Blank track')).toBeInTheDocument();
  expect(mocks.toast).toHaveBeenCalledWith('Template deleted.', 'success');
  expect(mocks.create).not.toHaveBeenCalled();
});

it('keeps a model on cancellation or a server rejection and reports the failure in a toast', async () => {
  mocks.list.mockResolvedValue([{ ...model, workspace_id: 'ws-qa' }]);
  mocks.remove.mockRejectedValue({ response: { data: { detail: 'Model is attached by reference.' } } });
  show();
  fireEvent.click(await screen.findByRole('button', { name: 'Delete Reading List template' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(mocks.remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Delete Reading List template' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Delete template' }));
  await waitFor(() => expect(mocks.toast).toHaveBeenCalledWith('Model is attached by reference.', 'error'));
  expect(screen.getByRole('button', { name: 'Delete Reading List template' })).toBeInTheDocument();
});

it('does not offer deletion for platform models or a model marked non-deletable', async () => {
  mocks.list.mockResolvedValue([model, { ...model, id: 'locked', workspace_id: 'ws-qa', can_delete: false }]);
  show();
  await screen.findAllByRole('button', { name: /Reading List Books/ });
  expect(screen.queryByRole('button', { name: /Delete .* template/ })).toBeNull();
});

it('chooses an existing App before picking its Track template and submits both choices', async () => {
  mocks.apps.mockResolvedValue([{ id: 'app-one', name: 'Client Work', lifecycle_state: 'active' }]);
  mocks.getModel.mockResolvedValue({ id: 'app-template', manifest: { scope: 'app', app: { tracks: [{ key: 'cases', name: 'Cases' }] } } });
  show();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add track to' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Add track to' }));
  fireEvent.click(await screen.findByRole('option', { name: /Client Work/ }));
  await screen.findByRole('button', { name: /^Cases / });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  fireEvent.change(screen.getByLabelText(/Track Name/), { target: { value: 'Client cases' } });
  expect(screen.getByText('Add to: Client Work')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Create track' }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ app_id: 'app-one', app_track_type_key: 'cases', title: 'Client cases' })));
  expect(mocks.create.mock.calls[0][0]).not.toHaveProperty('library_operational_model_id');
});

it('can leave an App-scoped flow for top-level Tracks without retaining the App or its template key', async () => {
  mocks.apps.mockResolvedValue([{ id: 'app-one', name: 'Client Work' }]);
  mocks.getModel.mockResolvedValue({ id: 'app-template', manifest: { scope: 'app', app: { tracks: [{ key: 'cases', name: 'Cases' }] } } });
  show({ appId: 'app-one' });
  await screen.findByRole('button', { name: /^Cases / });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add track to' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Add track to' }));
  fireEvent.click(await screen.findByRole('option', { name: /Top-level Tracks/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Create your own track' }));
  fireEvent.change(screen.getByLabelText(/Track Name/), { target: { value: 'Independent notes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create track' }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalled());
  expect(mocks.create.mock.calls[0][0]).not.toHaveProperty('app_id');
  expect(mocks.create.mock.calls[0][0]).not.toHaveProperty('app_track_type_key');
});

it('always shows the top-level destination and retries a failed App lookup', async () => {
  mocks.apps.mockRejectedValueOnce(new Error('offline')).mockResolvedValue([{ id: 'app-one', name: 'Client Work' }]);
  show();
  expect(screen.getByRole('button', { name: 'Add track to' })).toHaveTextContent('Top-level Tracks');
  fireEvent.click(await screen.findByRole('button', { name: 'Retry Apps' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add track to' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Add track to' }));
  expect(await screen.findByRole('option', { name: /Client Work/ })).toBeInTheDocument();
  expect(screen.queryByText(/operational model/i)).toBeNull();
});
