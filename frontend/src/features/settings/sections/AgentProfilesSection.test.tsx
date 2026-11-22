import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { AgentProfilesSection } from './AgentProfilesSection';

const mocks = vi.hoisted(() => ({
  scope: { activeWorkspace: { id: 'ws', name: 'Test Workspace', your_role: 'owner' } },
  get: vi.fn(), save: vi.fn(), action: vi.fn(), create: vi.fn(), publish: vi.fn(), renameWorkspaceAssistant: vi.fn(), setNativeChatProfiles: vi.fn(),
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
  click('Close');
  expect(screen.getByText('Discard unsaved changes?')).toBeVisible();
  click('Discard and go back');
  await open('Staff');
  expect(screen.getByLabelText('Profile name')).toHaveValue('Staff');
  click('Close');
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
  expect(within(screen.getByRole('dialog', { name: 'Integral AI' })).getByText('Your workspace assistant')).toBeVisible();
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
  click('Close');
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
  const dialog = screen.getByRole('dialog', { name: 'New profile' });
  fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'Field guide' } });
  fireEvent.change(screen.getByLabelText('Brief description'), { target: { value: 'A short guide' } });
  fireEvent.change(screen.getByLabelText('Persona'), { target: { value: 'Keep it practical.' } });
  expect(screen.getByRole('switch', { name: 'Public' })).toHaveAttribute('aria-checked', 'false');
  click('Save draft');
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith('ws', 1, expect.objectContaining({ audience: 'staff', description: 'A short guide', persona: 'Keep it practical.' })));
  expect(await screen.findByRole('dialog', { name: 'Field guide' })).toBe(dialog);
  expect(within(dialog).getByText('Draft saved.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Configure Field guide' })).toBeVisible();
  expect(screen.getByLabelText('Profile name')).toHaveValue('Field guide');
  click('Close');
  expect(await screen.findByRole('button', { name: 'Configure Field guide' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'New profile' })).toHaveFocus();
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
  click('Close');
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

it('keeps the index visible and restores focus when closing the workspace assistant dialog', async () => {
  mount();
  const opener = await screen.findByRole('button', { name: 'Configure Integral AI' });
  opener.focus();
  fireEvent.click(opener);
  const dialog = screen.getByRole('dialog', { name: 'Integral AI' });
  expect(dialog).toContainElement(screen.getByRole('heading', { name: 'Integral AI', level: 2 }));
  expect(screen.getByRole('heading', { name: 'Agent profiles' })).toBeVisible();
  click('Close');
  expect(screen.queryByRole('dialog', { name: 'Integral AI' })).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
});

it('opens creation over the card grid and guards unsaved changes on Escape', async () => {
  mount();
  const opener = await screen.findByRole('button', { name: 'New profile' });
  const grid = screen.getByRole('list', { name: 'Agent profiles' });
  fireEvent.click(opener);
  const dialog = screen.getByRole('dialog', { name: 'New profile' });
  expect(grid).toBeVisible();
  expect(within(dialog).getByRole('heading', { name: 'New profile', level: 2 })).toBeVisible();
  fireEvent.change(screen.getByLabelText('Profile name'), { target: { value: 'New draft' } });
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(dialog).toBeVisible();
  expect(screen.getByRole('button', { name: 'Keep editing' })).toHaveFocus();
  click('Keep editing');
  expect(screen.getByLabelText('Profile name')).toHaveValue('New draft');
  click('Close');
  click('Discard and go back');
  expect(dialog).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
  expect(screen.getByRole('list', { name: 'Agent profiles' })).toBe(grid);
  expect(mocks.create).not.toHaveBeenCalled();
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
  mount();
  await open('Public');
  const image = new File([Uint8Array.from(atob(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+k/GsAAAAASUVORK5CYII='
  ), character => character.charCodeAt(0))], 'fixture.png', { type: 'image/png' });
  fireEvent.change(screen.getByRole('dialog', { name: 'Public' }).querySelector('input[type=file]')!, { target: { files: [image] } });
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
  const dialog = screen.getByRole('dialog', { name: 'Public' });
  expect(dialog).toContainElement(screen.getByRole('tablist', { name: 'Profile editor' }));
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
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(dialog).toBeVisible();
  expect(screen.getByRole('button', { name: 'Keep editing' })).toHaveFocus();
  click('Keep editing');
  expect(screen.getByLabelText('Brief description')).toHaveValue('Still here');
  click('Close');
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

it('shows one card for each profile and manages published staff availability without republishing', async () => {
  const published = { ...settings, revision: 7, native_chat_profiles: ['other'], profiles: {
    ...settings.profiles, staff: { draft: staff, published: { ...staff, version: 2 } },
  } };
  mocks.get.mockResolvedValue(published);
  mocks.setNativeChatProfiles
    .mockResolvedValueOnce({ ...published, revision: 8, native_chat_profiles: ['other', 'staff'] })
    .mockResolvedValueOnce({ ...published, revision: 9 });
  mount();
  const grid = await screen.findByRole('list', { name: 'Agent profiles' });
  expect(within(grid).getAllByRole('listitem')).toHaveLength(3);
  const header = screen.getByRole('heading', { name: 'Agent profiles', level: 2 }).closest('header');
  expect(header).toContainElement(screen.getByRole('button', { name: 'New profile' }));
  expect(grid.parentElement).not.toContainElement(screen.getByRole('button', { name: 'New profile' }));
  expect(screen.queryByText('Workspace assistants and public profiles')).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Workspace chat profiles' })).not.toBeInTheDocument();
  await open('Staff');
  selectTab('Publish');
  const toggle = screen.getByRole('switch', { name: 'Show in workspace chat' });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(mocks.setNativeChatProfiles).toHaveBeenNthCalledWith(1, 'ws', 7, ['other', 'staff']);
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  expect(mocks.setNativeChatProfiles).toHaveBeenNthCalledWith(2, 'ws', 8, ['other']);
  expect(mocks.publish).not.toHaveBeenCalled();
  click('Close');
  await open('Public');
  selectTab('Publish');
  expect(screen.queryByRole('switch', { name: 'Show in workspace chat' })).not.toBeInTheDocument();
});

it('only stages workspace chat availability until the first publication succeeds', async () => {
  mocks.publish.mockResolvedValue(settings);
  mount();
  await open('Staff');
  selectTab('Publish');
  fireEvent.click(screen.getByRole('switch', { name: 'Show in workspace chat' }));
  expect(mocks.setNativeChatProfiles).not.toHaveBeenCalled();
  expect(mocks.publish).not.toHaveBeenCalled();
  expect(screen.getByText('On publish')).toBeVisible();
  click('Publish profile');
  await waitFor(() => expect(mocks.publish).toHaveBeenCalledWith('ws', 1, 'staff', true, []));
});
