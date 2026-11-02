import { act, render, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AiQuotaBanner } from './AiQuotaBanner';

const mocks = vi.hoisted(() => ({
  notify: vi.fn(), dismiss: vi.fn(), usage: vi.fn(),
  user: { id: 'user' }, scope: { workspaceId: 'workspace' },
  workspace: { your_role: 'owner' },
}));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: mocks.user }) }));
vi.mock('../../../context/ScopeContext', () => ({ useScope: () => ({ scope: mocks.scope, activeWorkspace: mocks.workspace }) }));
vi.mock('../../../hooks/usePlatformAdmin', () => ({ usePlatformAdmin: () => false }));
vi.mock('../../../components/system', () => ({
  useSystemNotifications: () => ({ notify: mocks.notify, dismiss: mocks.dismiss }),
}));
vi.mock('../../api/billing', () => ({ billingApi: { getAiUsage: mocks.usage } }));

const exhausted = {
  enforcement_enabled: true, is_unlimited: false, is_exhausted: true,
  agent_key_source: 'platform',
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><AiQuotaBanner /></MemoryRouter></QueryClientProvider>);
  return client;
}

describe('AI quota notification follows effective workspace key', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.workspace.your_role = 'owner';
    sessionStorage.clear();
    mocks.usage.mockResolvedValue(exhausted);
  });
  afterEach(cleanup);

  it('does not poll admin billing data for an ordinary member', async () => {
    mocks.workspace.your_role = 'member';
    mount();
    await waitFor(() => expect(mocks.dismiss).toHaveBeenCalled());
    expect(mocks.usage).not.toHaveBeenCalled();
    expect(mocks.notify).not.toHaveBeenCalled();
  });

  it('clears immediately after a usable owner key is saved and returns after revocation', async () => {
    const client = mount();
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledOnce());
    mocks.dismiss.mockClear();
    mocks.usage.mockResolvedValue({ ...exhausted, agent_key_source: 'byok' });
    act(() => client.setQueryData(['model-credentials', 'me'], { is_active: true }));
    await waitFor(() => expect(mocks.dismiss).toHaveBeenCalledWith('billing:ai-quota-exhausted'));
    mocks.usage.mockResolvedValue(exhausted);
    act(() => client.setQueryData(['model-credentials', 'me'], null));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledTimes(2));
  });

  it('retains the warning if a saved key is not the effective workspace route', async () => {
    const client = mount();
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledOnce());
    act(() => client.setQueryData(['model-credentials', 'me'], { is_active: true }));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledTimes(2));
  });

  it('does not warn when full access provides an unlimited allowance', async () => {
    mocks.usage.mockResolvedValue({ ...exhausted, is_unlimited: true, is_exhausted: false });
    mount();
    await waitFor(() => expect(mocks.dismiss).toHaveBeenCalled());
    expect(mocks.notify).not.toHaveBeenCalled();
  });

  it('does not restore a stale exhausted warning after a credential refresh', async () => {
    let resolveOld!: (value: unknown) => void;
    mocks.usage.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
    const client = mount();
    await waitFor(() => expect(mocks.usage).toHaveBeenCalledOnce());
    mocks.usage.mockResolvedValue({ ...exhausted, agent_key_source: 'byok' });
    act(() => client.setQueryData(['model-credentials', 'me'], { is_active: true }));
    await waitFor(() => expect(mocks.dismiss).toHaveBeenCalled());
    await act(async () => resolveOld(exhausted));
    expect(mocks.notify).not.toHaveBeenCalled();
  });
});
