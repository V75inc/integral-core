import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { modelCredentialsApi } from '../../../api/modelCredentials';
import { ModelCredentialsSection } from './ModelCredentialsSection';

vi.mock('../../../api/modelCredentials', async importOriginal => ({
  ...await importOriginal<typeof import('../../../api/modelCredentials')>(),
  modelCredentialsApi: { get: vi.fn(), upsert: vi.fn(), validate: vi.fn(), revoke: vi.fn() },
}));
vi.mock('../hooks/useAgentiveCapability', () => ({
  useAgentiveCapability: () => ({ agentKeyMode: 'hybrid' }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

function showSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ModelCredentialsSection /></QueryClientProvider>);
}

describe('native model credentials', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(modelCredentialsApi.get).mockResolvedValue(null); });

  it('shows the primary model and voice input without retired gear controls', async () => {
    showSettings();
    await screen.findByRole('button', { name: 'Save' });
    expect(screen.getByText('Primary')).toBeInTheDocument();
    expect(screen.getByText('Voice input')).toBeInTheDocument();
    for (const label of ['Quick replies', 'Complex tasks', 'Images']) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    expect(screen.getByRole('option', { name: /DeepSeek V4.1 Flash/ })).toHaveValue('deepseek-v4.1-flash');
  });

  it('saves only the selected native model and voice configuration', async () => {
    vi.mocked(modelCredentialsApi.upsert).mockResolvedValue({
      provider: 'ollama', model: 'deepseek-v4.1-flash', key_fingerprint: 'test', is_active: true,
    });
    showSettings();
    await screen.findByRole('button', { name: 'Save' });
    const key = document.querySelector('input[type="password"]');
    expect(key).not.toBeNull();
    fireEvent.change(key!, { target: { value: 'test-cloud-api-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(modelCredentialsApi.upsert).toHaveBeenCalledOnce());
    const body = vi.mocked(modelCredentialsApi.upsert).mock.calls[0][0];
    expect(body).toMatchObject({ provider: 'ollama', model: 'deepseek-v4.1-flash', api_key: 'test-cloud-api-key', speech_model: '' });
    expect(Object.keys(body).some(key => /^(light|heavy|vision)_/.test(key))).toBe(false);
  });
});
