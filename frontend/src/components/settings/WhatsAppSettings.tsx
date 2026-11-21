import { useState, useEffect, useCallback } from 'react';
import { Phone, Link2, CheckCircle2, XCircle, Loader2, Trash2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAgentive } from '../../context/AgentiveContext';
import {
  listChannelIdentities,
  unlinkChannelIdentity,
  initiateWhatsAppVerification,
  getWhatsAppChannelBinding,
  saveWhatsAppChannelBinding,
  disableWhatsAppChannelBinding,
  type WhatsAppChannelBinding,
} from '../../api/agentive';
import { Field } from '../../patterns';
import { Button, IconWell, LINE_ICON_STROKE } from '../ui';
import { Input, Text } from '../../ui';

function responseMessage(error: unknown, fallback: string): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const message = (error as { response?: { data?: { message?: unknown } } }).response?.data?.message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

function withoutSecrets(binding: WhatsAppChannelBinding): WhatsAppChannelBinding {
  const view = { ...binding } as WhatsAppChannelBinding & { api_key?: string; webhook_secret?: string };
  delete view.api_key;
  delete view.webhook_secret;
  return view;
}

interface ChannelIdentity {
  id: string;
  channel: string;
  channel_user_id: string;
  verified: boolean;
  verified_at?: string;
  preferences?: Record<string, unknown>;
  created_at?: string;
}

export function WhatsAppSettings({ onChange }: { onChange?: () => void } = {}) {
  const { showToast } = useToast();
  const { enabled } = useAgentive();
  const [identities, setIdentities] = useState<ChannelIdentity[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [phone, setPhone] = useState('');
  const [linkHint, setLinkHint] = useState('');
  const [step, setStep] = useState<'idle' | 'enter_phone' | 'await_message'>('idle');
  const [submitting, setSubmitting] = useState(false);
  const [binding, setBinding] = useState<WhatsAppChannelBinding | null>(null);
  const [bindingReady, setBindingReady] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [channelError, setChannelError] = useState('');
  const [channelAction, setChannelAction] = useState<'save' | 'disable' | null>(null);

  const loadIdentities = useCallback(async () => {
    if (!enabled) { setLoading(false); return; }
    setLoading(true);
    setLoadError(false);
    try {
      const res = await listChannelIdentities();
      setIdentities(res.identities || []);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  const loadBinding = useCallback(async () => {
    if (!enabled) { setBindingReady(true); return; }
    try {
      const res = await getWhatsAppChannelBinding();
      const next = res?.binding ? withoutSecrets(res.binding) : null;
      setBinding(next);
      setChannelError('');
      setBaseUrl(next?.jvconnect_base_url ?? '');
    } catch (error: unknown) {
      setChannelError(responseMessage(error, 'Could not load WhatsApp channel'));
    } finally {
      setBindingReady(true);
    }
  }, [enabled]);

  useEffect(() => { loadIdentities(); void loadBinding(); }, [loadIdentities, loadBinding]);

  const handleInitiate = async () => {
    if (!phone.trim()) { showToast('Enter your WhatsApp phone number', 'error'); return; }
    setSubmitting(true);
    try {
      const res = await initiateWhatsAppVerification(phone.trim());
      if (res.identity_id || res.otp_code) {
        if (res.verified) {
          showToast('WhatsApp number already linked!', 'success');
          await loadIdentities();
          onChange?.();
          setStep('idle');
          setPhone('');
        } else {
          const target = binding?.display_phone
            ? binding.display_phone
            : 'the WhatsApp business number';
          if (!res.pairing_code) {
            showToast('Pairing code unavailable. Please try again.', 'error');
            return;
          }
          setLinkHint(`From ${phone.trim()}, send ${res.pairing_code} to ${target}.`);
          setStep('await_message');
          showToast('Send the pairing code from that phone', 'info');
        }
      } else if (res.error) {
        showToast(res.error, 'error');
      }
    } catch (e: any) {
      showToast(e?.response?.data?.message || 'Failed to initiate verification', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleSaveChannel = async () => {
    const url = baseUrl.trim();
    const key = apiKey;
    if (!url || !key.trim()) {
      showToast('Enter the jvconnect base URL and API key', 'error');
      return;
    }
    setChannelAction('save');
    setChannelError('');
    try {
      const saved = await saveWhatsAppChannelBinding({ jvconnect_base_url: url, api_key: key });
      setApiKey('');
      if (saved?.binding) {
        const next = withoutSecrets(saved.binding);
        setBinding(next);
        setBaseUrl(next.jvconnect_base_url || url);
      }
      showToast('WhatsApp channel saved', 'success');
      await loadBinding();
    } catch (error: unknown) {
      const message = responseMessage(error, 'Could not save WhatsApp channel');
      setChannelError(message);
      showToast(message, 'error');
    } finally {
      setChannelAction(null);
    }
  };

  const handleDisableChannel = async () => {
    if (!binding?.id) return;
    setChannelAction('disable');
    setChannelError('');
    try {
      const saved = await disableWhatsAppChannelBinding(binding.id);
      if (saved?.binding) setBinding(withoutSecrets(saved.binding));
      showToast('WhatsApp channel disabled', 'success');
      await loadBinding();
    } catch (error: unknown) {
      const message = responseMessage(error, 'Could not disable WhatsApp channel');
      setChannelError(message);
      showToast(message, 'error');
    } finally {
      setChannelAction(null);
    }
  };

  const handleUnlink = async (id: string) => {
    try {
      await unlinkChannelIdentity(id);
      showToast('WhatsApp number unlinked', 'success');
      await loadIdentities();
      onChange?.();
    } catch {
      showToast('Failed to unlink', 'error');
    }
  };

  const whatsappIdentities = identities.filter((i) => i.channel === 'whatsapp');

  if (!enabled) {
    return (
      <div className="app-card p-5">
        <h2 className="font-display text-xl font-bold text-[var(--text)] flex items-center gap-2 mb-3">
          <Phone size={20} strokeWidth={LINE_ICON_STROKE} />
          WhatsApp
        </h2>
        <Text variant="body" tone="muted" as="p">
          WhatsApp is unavailable. Contact your administrator to check channel configuration.
        </Text>
      </div>
    );
  }

  if (loading || !bindingReady) {
    return (
      <div className="app-card p-5 flex items-center gap-3">
        <Loader2 size={18} className="animate-spin text-[var(--text-muted)]" />
        <span className="text-sm text-[var(--text-muted)]">Loading...</span>
      </div>
    );
  }

  if (loadError) return <div role="alert" className="app-card p-5"><Text as="p" variant="body-sm" tone="danger">Could not load WhatsApp numbers.</Text><Button variant="outline" size="sm" onClick={() => { void loadIdentities(); void loadBinding(); }}>Try again</Button></div>;

  return (
    <div className="app-card p-5">
      <h3 className="text-sm font-semibold flex items-center gap-2 mb-4">
        <IconWell size="sm" aria-hidden>
          <Phone size={16} strokeWidth={LINE_ICON_STROKE} />
        </IconWell>
        WhatsApp
      </h3>

      <p className="text-sm text-[var(--text-muted)] mb-4">
        Link your WhatsApp number to interact with Integral via WhatsApp messages.
        You'll be able to create entries, query tracks, and manage your workspace from any WhatsApp chat.
      </p>

      {/* Linked numbers */}
      {whatsappIdentities.length > 0 && (
        <div className="space-y-2 mb-4">
          <h3 className="text-sm font-medium text-[var(--text)]">Linked numbers</h3>
          {whatsappIdentities.map((ci) => (
            <div
              key={ci.id}
              className="flex items-center justify-between bg-[var(--panel-2)] rounded-[var(--radius-input)] px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <Phone size={14} className="text-[var(--text-muted)]" />
                <span className="text-sm font-mono">{ci.channel_user_id}</span>
                {ci.verified ? (
                  <span className="inline-flex items-center gap-1 text-xs text-[var(--success-fg)]">
                    <CheckCircle2 size={12} /> Verified
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs text-[var(--warn-fg)]">
                    <XCircle size={12} /> Unverified
                  </span>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                icon={<Trash2 size={13} strokeWidth={LINE_ICON_STROKE} />}
                onClick={() => handleUnlink(ci.id)}
              >
                Unlink
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Add new number */}
      {step === 'idle' && (
        <Button
          variant="outline"
          size="sm"
          icon={<Link2 size={13} strokeWidth={LINE_ICON_STROKE} />}
          onClick={() => { setStep('enter_phone'); setPhone(''); setLinkHint(''); }}
        >
          Link WhatsApp number
        </Button>
      )}

      {step === 'enter_phone' && (
        <div className="space-y-3">
          <label className="block">
            <span className="text-sm font-medium text-[var(--text)]">WhatsApp phone number</span>
            <input
              type="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+15556767191"
              className="mt-1 block w-full rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel-2)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--brand)]"
            />
          </label>
          <div className="flex gap-2">
            <Button variant="primary" size="sm" onClick={handleInitiate} loading={submitting}>
              Link this number
            </Button>
            <Button variant="ghost" size="sm" onClick={() => { setStep('idle'); setPhone(''); setLinkHint(''); }}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {step === 'await_message' && (
        <div className="space-y-3">
          <Text variant="body" tone="muted" as="p">{linkHint}</Text>
          <Button variant="ghost" size="sm" onClick={() => { setStep('idle'); setPhone(''); setLinkHint(''); }}>
            Done
          </Button>
        </div>
      )}

      <div className="mt-6 space-y-3 border-t border-[var(--panel-border)] pt-4">
        <Text variant="heading-sm" as="h4">WhatsApp channel</Text>
        {binding && (
          <div className="space-y-1">
            <Text variant="body-sm" tone="muted" as="p">{binding.jvconnect_base_url}</Text>
            <Text variant="body-sm" tone="muted" as="p">{binding.enabled ? 'Enabled' : 'Disabled'}</Text>
            <Text variant="body-sm" tone="muted" as="p">{binding.webhook_registered ? 'Webhook registered' : 'Webhook not registered'}</Text>
            {binding.api_key_last4 ? <Text variant="body-sm" as="p">API key ending in {binding.api_key_last4}</Text> : null}
            {binding.phone_number_id ? <Text variant="body-sm" as="p">Phone number id {binding.phone_number_id}</Text> : null}
            {binding.display_phone ? <Text variant="body-sm" as="p">Display phone {binding.display_phone}</Text> : null}
            <Text variant="body-sm" tone="muted" as="p">Other workspaces can use this number with the same API key.</Text>
            {binding.last_error?.trim() ? <Text variant="body-sm" tone="danger" as="p">{binding.last_error}</Text> : null}
          </div>
        )}
        {channelError ? <Text variant="body-sm" tone="danger" as="p" role="alert">{channelError}</Text> : null}
        <Field label="jvconnect base URL">
          <Input type="text" value={baseUrl} autoComplete="off" onChange={(event) => setBaseUrl(event.target.value)} />
        </Field>
        <Field label="API key">
          <Input type="password" value={apiKey} autoComplete="new-password" onChange={(event) => setApiKey(event.target.value)} />
        </Field>
        <div className="flex gap-2">
          <Button variant="primary" size="sm" onClick={() => void handleSaveChannel()} loading={channelAction === 'save'} disabled={channelAction !== null}>
            Save
          </Button>
          {binding?.id ? (
            <Button variant="outline" size="sm" onClick={() => void handleDisableChannel()} loading={channelAction === 'disable'} disabled={channelAction !== null}>
              Disconnect
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}