import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('../../../api/agentive', () => ({
  listChannelIdentities: vi.fn(), unlinkChannelIdentity: vi.fn(),
  initiateWhatsAppVerification: vi.fn(),
  getWhatsAppChannelBinding: vi.fn(), saveWhatsAppChannelBinding: vi.fn(),
  disableWhatsAppChannelBinding: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ showToast: vi.fn() }) }));
vi.mock('../../../context/AgentiveContext', () => ({ useAgentive: () => ({ enabled: true }) }));
import { listChannelIdentities, unlinkChannelIdentity, initiateWhatsAppVerification, getWhatsAppChannelBinding, saveWhatsAppChannelBinding, disableWhatsAppChannelBinding } from '../../../api/agentive';
import { WhatsAppSettings } from '../WhatsAppSettings';

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(listChannelIdentities).mockResolvedValue({ identities: [] });
  vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({ binding: null });
});
describe('WhatsApp notification setup', () => {
  it('shows a failed identity request and permits retry', async () => {
    vi.mocked(listChannelIdentities).mockRejectedValueOnce(new Error('Unavailable'));
    render(<WhatsAppSettings />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load WhatsApp numbers');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: 'Link WhatsApp number' })).toBeInTheDocument();
    expect(listChannelIdentities).toHaveBeenCalledTimes(2);
  });
  it('refreshes notification eligibility after verification', async () => {
    vi.mocked(initiateWhatsAppVerification).mockResolvedValue({ identity_id: 'phone-1', verified: false });
    vi.mocked(getWhatsAppChannelBinding).mockResolvedValue({
      binding: {
        id: 'bind-1', jvconnect_base_url: 'https://jvconnect.example', phone_number_id: 'pn-1',
        display_phone: '+15550001111', enabled: true, last_error: '', api_key_last4: '9911', webhook_registered: true,
      },
    });
    render(<WhatsAppSettings />);
    fireEvent.click(await screen.findByRole('button', { name: 'Link WhatsApp number' }));
    fireEvent.change(screen.getByLabelText('WhatsApp phone number'), { target: { value: '+15551234567' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link this number' }));
    expect(await screen.findByText('From +15551234567, send any message to +15550001111.')).toBeInTheDocument();
    expect(screen.queryByText(/1E200E|verification code/i)).not.toBeInTheDocument();
    expect(initiateWhatsAppVerification).toHaveBeenCalledWith('+15551234567');
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
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/9911/)).toBeInTheDocument();
    expect(saveWhatsAppChannelBinding).toHaveBeenCalledTimes(1);
    expect(saveWhatsAppChannelBinding).toHaveBeenCalledWith({ jvconnect_base_url: 'https://jvconnect.example', api_key: apiKey });
    expect(getWhatsAppChannelBinding).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('API key')).toHaveValue('');
    expect(screen.queryByDisplayValue(apiKey)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(apiKey);
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
});
