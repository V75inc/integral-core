import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { beforeEach, describe, expect, it, vi } from 'vitest';
const showToast = vi.hoisted(() => vi.fn());
vi.mock('../../../api/agentive', () => ({
  listChannelIdentities: vi.fn(), unlinkChannelIdentity: vi.fn(),
  initiateWhatsAppVerification: vi.fn(),
  getWhatsAppChannelBinding: vi.fn(), saveWhatsAppChannelBinding: vi.fn(),
  disableWhatsAppChannelBinding: vi.fn(),
  setWhatsAppCalling: vi.fn(),
  saveWhatsAppVoiceService: vi.fn(),
  reregisterWhatsAppWebhook: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ showToast }) }));
vi.mock('../../../context/AgentiveContext', () => ({ useAgentive: () => ({ enabled: true }) }));
import { listChannelIdentities, unlinkChannelIdentity, initiateWhatsAppVerification, getWhatsAppChannelBinding, saveWhatsAppChannelBinding, disableWhatsAppChannelBinding, setWhatsAppCalling, saveWhatsAppVoiceService, reregisterWhatsAppWebhook } from '../../../api/agentive';
import { WhatsAppSettings } from '../WhatsAppSettings';

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(listChannelIdentities).mockResolvedValue({ identities: [] });
  vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({ binding: null });
});
describe('WhatsApp notification setup', () => {
  it('links a personal number without loading the workspace line', async () => {
    render(<WhatsAppSettings personalOnly />);
    expect(await screen.findByRole('region', { name: 'Personal WhatsApp number' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Link WhatsApp number' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Workspace WhatsApp line' })).not.toBeInTheDocument();
    expect(getWhatsAppChannelBinding).not.toHaveBeenCalled();
  });
  it('shows a failed identity request and permits retry', async () => {
    vi.mocked(listChannelIdentities).mockRejectedValueOnce(new Error('Unavailable'));
    render(<WhatsAppSettings />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load WhatsApp numbers');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: 'Link WhatsApp number' })).toBeInTheDocument();
    expect(listChannelIdentities).toHaveBeenCalledTimes(2);
  });
  it('refreshes notification eligibility after verification', async () => {
    const phone = { id: 'phone-1', channel: 'whatsapp', channel_user_id: '+15551234567' };
    vi.mocked(listChannelIdentities)
      .mockResolvedValueOnce({ identities: [{ ...phone, verified: false }] })
      .mockResolvedValueOnce({ identities: [{ ...phone, verified: true }] });
    vi.mocked(initiateWhatsAppVerification).mockResolvedValue({ identity_id: 'phone-1', verified: false, pairing_code: 'fixture-pairing-code' });
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: 'pn-1',
        display_phone: '+15550001111', enabled: true, last_error: '', api_key_last4: '9911', webhook_registered: true,
      },
    });
    const onChange = vi.fn();
    render(<WhatsAppSettings onChange={onChange} />);
    expect(await screen.findByText('Unverified')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Link WhatsApp number' }));
    fireEvent.change(screen.getByLabelText('WhatsApp phone number'), { target: { value: '+15551234567' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link this number' }));
    expect(await screen.findByText('From +15551234567, send fixture-pairing-code to +15550001111.')).toBeInTheDocument();
    expect(screen.queryByText(/1E200E|verification code/i)).not.toBeInTheDocument();
    expect(initiateWhatsAppVerification).toHaveBeenCalledWith('+15551234567');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByText('Verified')).toBeInTheDocument();
    expect(screen.queryByText('Unverified')).not.toBeInTheDocument();
    expect(listChannelIdentities).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenCalledOnce();
  });
  it('refreshes notification eligibility after unlinking', async () => {
    vi.mocked(listChannelIdentities).mockResolvedValueOnce({ identities: [{ id: 'phone-1', channel: 'whatsapp', channel_user_id: '+15551234567', verified: true }] });
    vi.mocked(unlinkChannelIdentity).mockResolvedValue({ success: true });
    const onChange = vi.fn();
    render(<WhatsAppSettings onChange={onChange} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
    expect(unlinkChannelIdentity).toHaveBeenCalledWith('phone-1');
  });
  it('saves the jvconnect URL and API key once, then clears the key and shows the last four', async () => {
    const apiKey = 'jvk_channel_secret_9911';
    vi.mocked(saveWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: '',
        display_phone: '', enabled: false, last_error: '', api_key_last4: '', webhook_registered: false,
      },
    });
    vi.mocked(getWhatsAppChannelBinding)
      .mockResolvedValueOnce({ binding: null })
      .mockResolvedValueOnce({
        binding: {
          id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: 'pn-1',
          display_phone: '+15551230000', enabled: true, last_error: '', api_key_last4: '9911', webhook_registered: true,
        },
      });
    render(<WhatsAppSettings />);
    fireEvent.change(await screen.findByLabelText('jvconnect base URL'), { target: { value: 'https://jvconnect.example' } });
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: apiKey } });
    fireEvent.click(screen.getByRole('button', { name: 'Add connection' }));
    expect(await screen.findByText(/9911/)).toBeInTheDocument();
    expect(saveWhatsAppChannelBinding).toHaveBeenCalledTimes(1);
    expect(saveWhatsAppChannelBinding).toHaveBeenCalledWith({ jvconnect_base_url: 'https://jvconnect.example', api_key: apiKey, expected_binding_id: null });
    expect(getWhatsAppChannelBinding).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('API key')).toHaveValue('');
    expect(screen.queryByDisplayValue(apiKey)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(apiKey);
  });
  it('configures the existing line with its expected ID and withholds workspace mutations from members', async () => {
    const binding = { id: 'line', jvconnect_base_url: 'https://line.invalid', enabled: true };
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({ binding });
    vi.mocked(saveWhatsAppChannelBinding).mockResolvedValue({ binding });
    const { rerender } = render(<WhatsAppSettings />);
    fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'jvk_synthetic' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(saveWhatsAppChannelBinding).toHaveBeenCalledWith({
      jvconnect_base_url: 'https://line.invalid', api_key: 'jvk_synthetic', expected_binding_id: 'line',
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Disconnect' })).toBeEnabled());
    rerender(<WhatsAppSettings canManageLine={false} />);
    expect(screen.queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Link WhatsApp number' })).toBeVisible();
  });

  it('shows a binding last_error on the card', async () => {
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-err', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: '',
        display_phone: '', enabled: false, last_error: 'Public callback URL is not configured',
        api_key_last4: 'abcd', webhook_registered: false,
      },
    });
    render(<WhatsAppSettings />);
    expect(await screen.findByText('Public callback URL is not configured')).toBeInTheDocument();
  });
  it('disables the WhatsApp channel without unlinking the phone number', async () => {
    vi.mocked(listChannelIdentities).mockResolvedValue({
      identities: [{ id: 'phone-1', channel: 'whatsapp', channel_user_id: '+15551234567', verified: true }],
    });
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-9', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: 'pn-9',
        display_phone: '+15551234567', enabled: true, last_error: '', api_key_last4: 'wxyz', webhook_registered: true,
      },
    });
    vi.mocked(disableWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-9', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: 'pn-9',
        display_phone: '+15551234567', enabled: false, last_error: '', api_key_last4: 'wxyz', webhook_registered: false,
      },
    });
    render(<WhatsAppSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(disableWhatsAppChannelBinding).toHaveBeenCalledTimes(1));
    expect(disableWhatsAppChannelBinding).toHaveBeenCalledWith('bind-9');
    expect(unlinkChannelIdentity).not.toHaveBeenCalled();
  });

  it('turns voice calling on and off from the last saved grant', async () => {
    const off = {
      id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', enabled: true,
      calling_enabled: false, calling_grant_id: 'edge-1', calling_revision: 0,
    };
    const on = { ...off, calling_enabled: true, calling_revision: 1 };
    vi.mocked(getWhatsAppChannelBinding)
      .mockResolvedValueOnce({ binding: off })
      .mockResolvedValueOnce({ binding: on })
      .mockResolvedValue({ binding: { ...on, calling_enabled: false, calling_revision: 2 } });
    vi.mocked(setWhatsAppCalling).mockResolvedValue({ binding: on });
    render(<WhatsAppSettings />);
    const box = await screen.findByRole('button', { name: /Voice calling/ });
    expect(box).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/Text messaging stays available when calling is off/)).toBeInTheDocument();
    fireEvent.click(box);
    await waitFor(() => expect(setWhatsAppCalling).toHaveBeenCalledTimes(1));
    expect(setWhatsAppCalling).toHaveBeenCalledWith({
      calling_enabled: true, expected_grant_id: 'edge-1', expected_revision: 0,
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Voice calling/ })).toHaveAttribute(
        'aria-pressed',
        'true',
      ),
    );
    expect(getWhatsAppChannelBinding).toHaveBeenCalledTimes(2);
    expect(await screen.findByLabelText('jvvoice base URL')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Voice calling/ }));
    await waitFor(() => expect(setWhatsAppCalling).toHaveBeenCalledTimes(2));
    expect(setWhatsAppCalling).toHaveBeenLastCalledWith({
      calling_enabled: false, expected_grant_id: 'edge-1', expected_revision: 1,
    });
  });

  it('does not send voice calling for members or view mode', async () => {
    const binding = {
      id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', enabled: true,
      calling_enabled: true, calling_grant_id: 'edge-1', calling_revision: 3,
    };
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({ binding });
    const { rerender } = render(<WhatsAppSettings canManageLine={false} />);
    const memberBox = await screen.findByRole('button', { name: /Voice calling/ });
    expect(memberBox).toBeDisabled();
    expect(memberBox).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(memberBox);
    expect(setWhatsAppCalling).not.toHaveBeenCalled();
    rerender(<WhatsAppSettings dialog={{ mode: 'View', onClose: vi.fn() }} />);
    const viewBox = screen.getByRole('button', { name: /Voice calling/ });
    expect(viewBox).toBeDisabled();
    fireEvent.click(viewBox);
    expect(setWhatsAppCalling).not.toHaveBeenCalled();
  });

  it('hides voice calling when the workspace has no line', async () => {
    render(<WhatsAppSettings />);
    expect(await screen.findByRole('button', { name: 'Link WhatsApp number' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Voice calling/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Voice calling')).not.toBeInTheDocument();
  });

  it('hides the jvvoice service until calling is on, and never collects LiveKit keys', async () => {
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', enabled: true,
        calling_enabled: false, calling_grant_id: 'edge-1', calling_revision: 0,
      },
    });
    const { unmount } = render(<WhatsAppSettings />);
    expect(await screen.findByLabelText('jvconnect base URL')).toBeInTheDocument();
    expect(screen.queryByLabelText('jvvoice base URL')).not.toBeInTheDocument();
    const html = document.documentElement.innerHTML;
    for (const secret of ['LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET']) {
      expect(html).not.toContain(secret);
    }
    unmount();
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', enabled: true,
        calling_enabled: true, calling_grant_id: 'edge-1', calling_revision: 1,
        jvvoice_base_url: 'https://voice.example', jvvoice_api_key_last4: 'cret',
        jvconnect_calling_status: 'ENABLED',
      },
    });
    render(<WhatsAppSettings />);
    expect(await screen.findByLabelText('jvvoice base URL')).toBeInTheDocument();
    expect(screen.getByLabelText('jvvoice API key')).toBeInTheDocument();
    expect(screen.getByText('JV Connect calling on')).toBeInTheDocument();
    expect(document.documentElement.innerHTML).not.toContain('LIVEKIT_API_SECRET');
    vi.mocked(saveWhatsAppVoiceService).mockResolvedValue({
      binding: {
        id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', enabled: true,
        calling_enabled: true, calling_grant_id: 'edge-1', calling_revision: 1,
        jvvoice_base_url: 'https://voice.example', jvvoice_api_key_last4: 'cret',
        jvconnect_calling_status: 'ENABLED',
      },
    });
    fireEvent.change(screen.getByLabelText('jvvoice API key'), { target: { value: 'voice-secret-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save voice service' }));
    await waitFor(() => expect(saveWhatsAppVoiceService).toHaveBeenCalledWith({
      jvvoice_base_url: 'https://voice.example', api_key: 'voice-secret-key',
    }));
  });

  it('reloads voice calling when the server rejects the change', async () => {
    const binding = {
      id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', enabled: true,
      calling_enabled: false, calling_grant_id: 'edge-1', calling_revision: 0,
    };
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({ binding });
    vi.mocked(setWhatsAppCalling).mockRejectedValueOnce({
      response: { status: 409, data: { message: 'WhatsApp grant changed. Refresh and try again.' } },
    });
    render(<WhatsAppSettings />);
    fireEvent.click(await screen.findByRole('button', { name: /Voice calling/ }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'WhatsApp grant changed. Refresh and try again.', 'error',
    ));
    await waitFor(() => expect(getWhatsAppChannelBinding).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('button', { name: /Voice calling/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );

    vi.mocked(setWhatsAppCalling).mockRejectedValueOnce({
      response: { status: 403, data: { message: 'Only workspace owners and admins can manage WhatsApp' } },
    });
    fireEvent.click(screen.getByRole('button', { name: /Voice calling/ }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'Only workspace owners and admins can manage WhatsApp', 'error',
    ));
    await waitFor(() => expect(getWhatsAppChannelBinding).toHaveBeenCalledTimes(3));
  });

  it('reregisters the webhook from the connected line', async () => {
    const binding = {
      id: 'bind-9', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: 'pn-9',
      display_phone: '+15551234567', enabled: true, last_error: '', api_key_last4: 'wxyz', webhook_registered: true,
    };
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({ binding });
    vi.mocked(reregisterWhatsAppWebhook).mockResolvedValue({ binding });
    render(<WhatsAppSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reregister webhook' }));
    await waitFor(() => expect(reregisterWhatsAppWebhook).toHaveBeenCalledTimes(1));
    expect(showToast).toHaveBeenCalledWith('Webhook reregistered', 'success');
  });

  it('shows when JV Connect calling settings are unavailable', async () => {
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', enabled: true,
        calling_enabled: true, calling_grant_id: 'edge-1', calling_revision: 1,
        jvconnect_calling_error: 'This JV Connect is missing calling settings. Upgrade JV Connect and try again.',
      },
    });
    render(<WhatsAppSettings />);
    expect(await screen.findByText('JV Connect calling settings unavailable')).toBeInTheDocument();
    expect(screen.getByText(/Upgrade JV Connect and try again/)).toBeInTheDocument();
  });
});
