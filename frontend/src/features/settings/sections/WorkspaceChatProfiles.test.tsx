import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { WorkspaceChatProfiles } from './WorkspaceChatProfiles';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  setNativeChatProfiles: vi.fn(),
}));
vi.mock('../../../api/connectionProfiles', () => ({ connectionProfilesApi: mocks }));

const settings = {
  revision: 7,
  native_chat_profiles: ['assigned'],
  profiles: {
    assigned: { published: { key: 'assigned', audience: 'staff', name: 'Already offered' } },
    available: { published: { key: 'available', audience: 'staff', name: 'Available profile' } },
    public: { published: { key: 'public', name: 'Public profile' } },
    draft: { draft: { key: 'draft', audience: 'staff', name: 'Unpublished profile' } },
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  mocks.get.mockResolvedValue(settings);
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><WorkspaceChatProfiles workspaceId="workspace" /></QueryClientProvider>);
}

it('adds only an explicitly selected published workspace profile and preserves active assignments', async () => {
  mocks.setNativeChatProfiles.mockResolvedValue({
    ...settings, revision: 8, native_chat_profiles: ['assigned', 'available'],
  });
  mount();
  const select = await screen.findByRole('combobox', { name: 'Published profile' });
  expect(screen.getByText('Already offered')).toBeVisible();
  expect(screen.queryByRole('option', { name: 'Public profile' })).not.toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'Unpublished profile' })).not.toBeInTheDocument();
  const add = screen.getByRole('button', { name: 'Add to workspace chat' });
  expect(add).toBeDisabled();
  fireEvent.change(select, { target: { value: 'available' } });
  expect(mocks.setNativeChatProfiles).not.toHaveBeenCalled();
  fireEvent.click(add);
  expect(await screen.findByText('Profile added to workspace chat.')).toBeVisible();
  expect(mocks.setNativeChatProfiles).toHaveBeenCalledWith('workspace', 7, ['assigned', 'available']);
  expect(screen.getByText('Already offered')).toBeVisible();
  expect(screen.getByText('Available profile')).toBeVisible();
});
