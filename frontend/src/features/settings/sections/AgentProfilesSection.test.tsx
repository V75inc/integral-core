import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { AgentProfilesSection } from './AgentProfilesSection';

const mocks = vi.hoisted(() => ({
  scope: { activeWorkspace: { id: 'ws', name: 'Test Workspace', your_role: 'owner' } },
  get: vi.fn(), save: vi.fn(), action: vi.fn(), create: vi.fn(), publish: vi.fn(), renameWorkspaceAssistant: vi.fn(),
}));
vi.mock('../../../context/ScopeContext', () => ({ useScopeOptional: () => mocks.scope }));
vi.mock('../../../api/connectionProfiles', () => ({ connectionProfilesApi: mocks }));

const draft = { key: 'public', name: 'Public', persona: '', tools: ['read'], omissions: [] as string[] };
const staff = { ...draft, key: 'staff', name: 'Staff', tools: [] as string[] };
const settings = {
  revision: 1, profiles: { public: { draft }, staff: { draft: staff } },
  catalogue: { public: { read: { label: 'Read', description: 'Published read' } }, staff: {} },
  connections: [], bindings: {}, native_chat_profiles: [],
};
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const selectTab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }));
const open = async (name: string) => fireEvent.click(await screen.findByRole('button', { name: `Configure ${name}` }));

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { ...render(<QueryClientProvider client={client}><AgentProfilesSection /></QueryClientProvider>), client };
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.scope.activeWorkspace = { id: 'ws', name: 'Test Workspace', your_role: 'owner' };
  mocks.get.mockResolvedValue(settings);
});

it.each(['member', 'guest'])('withholds management from a workspace %s', (role) => {
  mocks.scope.activeWorkspace.your_role = role;
  mount();
  expect(screen.getByText(/Only workspace admins and the owner/)).toBeVisible();
  expect(mocks.get).not.toHaveBeenCalled();
});

it.each(['owner', 'admin'])('allows profile management for a workspace %s', async (role) => {
  mocks.scope.activeWorkspace.your_role = role;
  mount();
  expect(await screen.findByRole('button', { name: 'New profile' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Configure Integral AI' })).toBeVisible();
  expect(mocks.get).toHaveBeenCalledWith('ws');
});

it('drops management when switching from an administered workspace to a member workspace', async () => {
  mocks.scope.activeWorkspace.your_role = 'admin';
  const { client, rerender } = mount();
  await open('Integral AI');
  expect(screen.getByRole('textbox', { name: 'Persona' })).toBeVisible();
  mocks.scope.activeWorkspace = { id: 'foreign', name: 'Other workspace', your_role: 'member' };
  rerender(<QueryClientProvider client={client}><AgentProfilesSection /></QueryClientProvider>);
  expect(screen.getByText(/Only workspace admins and the owner/)).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Persona' })).not.toBeInTheDocument();
  expect(mocks.get).not.toHaveBeenCalledWith('foreign');
});

it('lands in the index and keeps edits isolated when opening another profile', async () => {
  mount();
  await screen.findByRole('button', { name: 'New profile' });
  expect(screen.queryByLabelText('Profile name')).not.toBeInTheDocument();
  await open('Public');
  fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'Unsaved public name' } });
  click('Back to profiles');
  expect(screen.getByText('Discard unsaved changes?')).toBeVisible();
  click('Discard and go back');
  await open('Staff');
  expect(screen.getByLabelText('Profile name')).toHaveValue('Staff');
  click('Back to profiles');
  await open('Public');
  expect(screen.getByLabelText('Profile name')).toHaveValue('Public');
});

it('uses shared identity controls for the Workspace Assistant and saves through its existing endpoint', async () => {
  mount();
  await open('Integral AI');
  const name = screen.getByRole('textbox', { name: 'Profile name' });
  const avatar = screen.getByRole('button', { name: 'Choose avatar' });
  expect(avatar.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByText('Brief description')).toBeVisible();
  expect(screen.getByText('Your workspace assistant')).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Brief description' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Persona' })).toHaveValue('');
  expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save assistant' })).toBeDisabled();
  mocks.renameWorkspaceAssistant.mockResolvedValue({
    ...settings, revision: 2, workspace_assistant: {
      key: 'workspace-assistant', name: 'Workspace guide', description: 'Your workspace assistant', icon: 'integral',
      persona: 'Keep answers brief.',
    },
  });
  fireEvent.change(name, { target: { value: 'Workspace guide' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Persona' }), { target: { value: 'Keep answers brief.' } });
  click('Save assistant');
  await screen.findByText('Workspace Assistant saved.');
  expect(mocks.renameWorkspaceAssistant).toHaveBeenCalledWith('ws', 1, 'Workspace guide', { persona: 'Keep answers brief.' });
  expect(mocks.save).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Save assistant' })).toBeDisabled();
  click('Back to profiles');
  await open('Workspace guide');
  expect(screen.getByRole('textbox', { name: 'Persona' })).toHaveValue('Keep answers brief.');
});

it('opens a newly created profile and shows it again in the index', async () => {
  mount();
  await screen.findByRole('button', { name: 'New profile' });
  mocks.create.mockResolvedValue({
    ...settings, revision: 2, created_profile: 'new',
    profiles: { ...settings.profiles, new: { draft: { ...staff, key: 'new', audience: 'staff', name: 'Field guide' } } },
  });
  click('New profile');
  fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'Field guide' } });
  fireEvent.change(screen.getByLabelText('Brief description'), { target: { value: 'A short guide' } });
  fireEvent.change(screen.getByLabelText('Persona'), { target: { value: 'Keep it practical.' } });
  expect(screen.getByRole('switch', { name: 'Public' })).toHaveAttribute('aria-checked', 'false');
  click('Save draft');
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith('ws', 1, expect.objectContaining({ audience: 'staff', description: 'A short guide', persona: 'Keep it practical.' })));
  expect(await screen.findByRole('heading', { name: 'Field guide' })).toBeVisible();
  expect(screen.getByLabelText('Profile name')).toHaveValue('Field guide');
  click('Back to profiles');
  expect(await screen.findByRole('button', { name: 'Configure Field guide' })).toBeVisible();
});

it('uses primary Next on both steps and disables secondary Save for pristine, reverted, and saved drafts', async () => {
  mount();
  const save = () => screen.getByRole('button', { name: 'Save draft' });
  const next = () => screen.getByRole('button', { name: 'Next' });
  fireEvent.click(await screen.findByRole('button', { name: 'New profile' }));
  expect(save()).toBeDisabled();
  expect(save()).toHaveAttribute('data-variant', 'secondary');
  expect(next()).toHaveAttribute('data-variant', 'primary');
  fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'New draft' } });
  expect(save()).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: '' } });
  expect(save()).toBeDisabled();
  click('Back to profiles');
  await open('Public');
  expect(save()).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Brief description'), { target: { value: 'Keep this' } });
  click('Next');
  expect(screen.getByRole('tabpanel', { name: 'Capabilities' })).toBeVisible();
  expect(next()).toHaveAttribute('data-variant', 'primary');
  expect(save()).toBeEnabled();
  click('Next');
  expect(screen.getByRole('tabpanel', { name: 'Publish' })).toBeVisible();
  mocks.save.mockResolvedValue({
    ...settings, revision: 2, profiles: { ...settings.profiles, public: { draft: { ...draft, description: 'Keep this' } } },
  });
  click('Save draft');
  await screen.findByText('Draft saved.');
  expect(save()).toBeDisabled();
  selectTab('Identity');
  fireEvent.change(screen.getByLabelText('Brief description'), { target: { value: 'Changed' } });
  expect(save()).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Brief description'), { target: { value: 'Keep this' } });
  expect(save()).toBeDisabled();
  click('Next');
  const access = screen.getByLabelText('Access for Read');
  fireEvent.change(access, { target: { value: 'blocked' } });
  expect(save()).toBeEnabled();
  fireEvent.change(access, { target: { value: 'allowed' } });
  expect(save()).toBeDisabled();
});

it('shows active routes and sends only new eligible selections through Publish', async () => {
  const withRoutes = {
    ...settings, revision: 3, native_chat_profiles: ['staff'],
    profiles: { ...settings.profiles, staff: { draft: staff, published: { ...staff, version: 2 } } },
    connections: [
      { id: 'active', label: 'Existing route', audience: 'staff', publish_assignable: true },
      { id: 'ready', label: 'Available route', audience: 'staff', publish_assignable: true },
      { id: 'fixture', label: 'Verification route', audience: 'staff', verification_path: '/fixture' },
    ],
    bindings: { active: 'staff' },
  };
  mocks.get.mockResolvedValue(withRoutes);
  mocks.publish.mockResolvedValue(withRoutes);
  mount();
  await open('Staff');
  selectTab('Publish');
  expect(screen.getByText('Assigned to this profile')).toBeVisible();
  expect(screen.getByText('Offered', { exact: true })).toBeVisible();
  expect(screen.queryByText('Verification route')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'Verification route' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Publish profile' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Available route' }));
  expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
  click('Publish profile');
  await waitFor(() => expect(mocks.publish).toHaveBeenCalledWith('ws', 3, 'staff', false, ['ready']));
});

it('places the shared back control above the base and create titles and restores opener focus', async () => {
  mount();
  for (const [opener, title] of [['Configure Integral AI', 'Integral AI'], ['New profile', 'New profile']]) {
    const indexButton = await screen.findByRole('button', { name: opener });
    fireEvent.click(indexButton);
    const back = screen.getByRole('button', { name: 'Back to profiles' });
    const heading = screen.getByRole('heading', { name: title });
    expect(back.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(back);
    expect(await screen.findByRole('button', { name: opener })).toHaveFocus();
  }
});

it('shows avatar and instructions directly and saves an explicit preset as a draft', async () => {
  mount();
  await open('Public');
  expect(screen.getByRole('region', { name: 'Appearance' })).toBeVisible();
  expect(screen.getByLabelText('Persona')).toBeVisible();
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole('button', { name: 'Choose avatar' }), { key: 'Enter' });
  expect(await screen.findByRole('menuitemradio', { name: 'Integral', checked: true })).toBeVisible();
  const book = screen.getByRole('menuitemradio', { name: 'Book', checked: false });
  expect(book.textContent).toBe('');
  expect(book.querySelector('svg')).toBeInTheDocument();
  fireEvent.click(book);
  mocks.save.mockResolvedValue(settings);
  click('Save draft');
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith('ws', 1, expect.objectContaining({ icon: 'book', avatar: null })));
});

it('takes a local file for avatar preview and sends it only on save', async () => {
  const { container } = mount();
  await open('Public');
  const image = new File([Uint8Array.from(atob(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+k/GsAAAAASUVORK5CYII='
  ), character => character.charCodeAt(0))], 'fixture.png', { type: 'image/png' });
  fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [image] } });
  await waitFor(() => expect(screen.getByRole('img', { name: 'Avatar preview' }).querySelector('img')).toBeInTheDocument());
  const preview = screen.getByRole('img', { name: 'Avatar preview' }).querySelector('img')?.getAttribute('src');
  expect(preview).toMatch(/^data:image\/png;base64,/);
  expect(mocks.save).not.toHaveBeenCalled();
  mocks.save.mockResolvedValue(settings);
  click('Save draft');
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith('ws', 1, expect.objectContaining({ avatar: preview })));
});

it('saves explicit capability state and keeps the Publish tab focused on availability', async () => {
  mount();
  await open('Public');
  selectTab('Capabilities');
  fireEvent.change(screen.getByLabelText('Access for Read'), { target: { value: 'blocked' } });
  expect(screen.getByLabelText('Access for Read')).toHaveValue('blocked');
  selectTab('Publish');
  expect(screen.queryByRole('button', { name: 'Preview draft' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Draft summary' })).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Availability' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Public web chat' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Workspace chat' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /chat link|embed/i })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Publish profile' })).toBeDisabled();
  const saved = { ...settings, revision: 2, profiles: { ...settings.profiles, public: { draft: { ...draft, omissions: ['read'], tools: [] } } } };
  mocks.save.mockResolvedValue(saved);
  click('Save draft');
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith('ws', 1, expect.objectContaining({ omissions: ['read'], tools: [] })));
  await screen.findByText('Draft saved.');
  expect(screen.getByRole('button', { name: 'Publish profile' })).toBeEnabled();
  expect(mocks.action).not.toHaveBeenCalled();
});

it('preserves the edited revision and field value after a server refetch and save conflict', async () => {
  const view = mount();
  await open('Public');
  fireEvent.change(screen.getByLabelText('Brief description'), { target: { value: 'Be brief' } });
  act(() => { view.client.setQueryData(['connection-profiles', 'ws'], { ...settings, revision: 2 }); });
  mocks.save.mockRejectedValue({ response: { data: { message: 'Profiles changed. Reload and try again.' } } });
  click('Save draft');
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith('ws', 1, expect.objectContaining({ description: 'Be brief' })));
  expect(await screen.findByRole('alert')).toHaveTextContent('Profiles changed');
  expect(screen.getByLabelText('Brief description')).toHaveValue('Be brief');
});


it('moves between tabs with the keyboard, retains edits, and returns focus to the row', async () => {
  mount();
  await open('Public');
  const back = screen.getByRole('button', { name: 'Back to profiles' });
  const heading = screen.getByRole('heading', { name: 'Public' });
  expect(back.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Brief description'), { target: { value: 'Still here' } });
  const identity = screen.getByRole('tab', { name: 'Identity' });
  const capabilities = screen.getByRole('tab', { name: 'Capabilities' });
  identity.focus();
  fireEvent.keyDown(identity, { key: 'ArrowRight' });
  expect(capabilities).toHaveFocus();
  expect(capabilities).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tabpanel', { name: 'Capabilities' })).toHaveAttribute('id', capabilities.getAttribute('aria-controls'));
  fireEvent.keyDown(capabilities, { key: 'Home' });
  expect(screen.getByLabelText('Brief description')).toHaveValue('Still here');
  click('Back to profiles');
  expect(screen.getByRole('button', { name: 'Keep editing' })).toHaveFocus();
  click('Discard and go back');
  expect(screen.getByRole('button', { name: 'Configure Public' })).toHaveFocus();
});

it('lists unavailable allowed and blocked tasks and removes them only after an explicit draft edit', async () => {
  mocks.get.mockResolvedValue({
    ...settings,
    profiles: { ...settings.profiles, public: { draft: {
      ...draft, tools: ['read', 'published_stock'], omissions: ['prepare_inquiry'],
    } } },
  });
  mount();
  await open('Public');
  click('Review unavailable tasks');
  expect(screen.getByRole('tab', { name: 'Capabilities' })).toHaveFocus();
  expect(screen.getByText('Published stock').parentElement).toHaveTextContent('Marked allowed');
  expect(screen.getByText('Prepare inquiry').parentElement).toHaveTextContent('Marked blocked');
  expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
  click('Remove unavailable tasks from draft');
  expect(mocks.save).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Access for Read')).toHaveValue('allowed');
  mocks.save.mockResolvedValue(settings);
  click('Save draft');
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith('ws', 1, expect.objectContaining({ tools: ['read'], omissions: [] })));
});
