import { useState, useEffect, useCallback, type ReactNode } from 'react';
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
  setWhatsAppCalling,
  saveWhatsAppVoiceService,
  reregisterWhatsAppWebhook,
  type WhatsAppChannelBinding,
} from '../../api/agentive';
import { Field } from '../../patterns';
import { StatusPill, ToggleRow } from '../../features/settings/components/Field';
import { Button, IconWell, LINE_ICON_STROKE } from '../ui';
import { WHATSAPP_CONNECTOR } from '../../features/settings/connectors/connectorDefinitions';
import { Input, Text } from '../../ui';
import { FormDialog } from '../../templates';

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

export type WhatsAppDialogMode = 'Add' | 'Configure' | 'View';

export interface WhatsAppDialog {
  mode: WhatsAppDialogMode;
  onClose: () => void;
}

export function WhatsAppSettings({ onChange, onBindingChange, canManageLine = true, dialog }: {
  onChange?: () => void;
  onBindingChange?: () => void;
  canManageLine?: boolean;
  dialog?: WhatsAppDialog;
} = {}) {
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
  const [bindingLoadFailed, setBindingLoadFailed] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [channelError, setChannelError] = useState('');
  const [channelAction, setChannelAction] = useState<'save' | 'disable' | 'calling' | 'voice' | 'webhook' | null>(null);
  const [voiceUrl, setVoiceUrl] = useState('');
  const [voiceKey, setVoiceKey] = useState('');

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
      setBindingLoadFailed(false);
      setBaseUrl(next?.jvconnect_base_url ?? '');
      setVoiceUrl(next?.jvvoice_base_url ?? '');
      setVoiceKey('');
    } catch (error: unknown) {
      setBindingLoadFailed(true);
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
    if (!canManageLine || bindingLoadFailed) return;
    const url = baseUrl.trim();
    const key = apiKey;
    if (!url || !key.trim()) {
      showToast('Enter the jvconnect base URL and API key', 'error');
      return;
    }
    setChannelAction('save');
    setChannelError('');
    try {
      const saved = await saveWhatsAppChannelBinding({
        jvconnect_base_url: url, api_key: key, expected_binding_id: binding?.id ?? null,
      });
      setApiKey('');
      if (saved?.binding) {
        const next = withoutSecrets(saved.binding);
        setBinding(next);
        setBaseUrl(next.jvconnect_base_url || url);
      }
      showToast(binding ? 'WhatsApp connection updated' : 'WhatsApp connection added', 'success');
      onBindingChange?.();
      await loadBinding();
    } catch (error: unknown) {
      const message = responseMessage(error, 'Could not save WhatsApp channel');
      setChannelError(message);
      showToast(message, 'error');
    } finally {
      setChannelAction(null);
    }
  };

  const handleReregisterWebhook = async () => {
    if (!canManageLine || !binding?.id || bindingLoadFailed) return;
    setChannelAction('webhook');
    setChannelError('');
    try {
      const saved = await reregisterWhatsAppWebhook();
      if (saved?.binding) setBinding(withoutSecrets(saved.binding));
      showToast('Webhook reregistered', 'success');
      onBindingChange?.();
      await loadBinding();
    } catch (error: unknown) {
      const message = responseMessage(error, 'Could not reregister the webhook');
      setChannelError(message);
      showToast(message, 'error');
    } finally {
      setChannelAction(null);
    }
  };

  const handleDisableChannel = async () => {
    if (!canManageLine || !binding?.id || bindingLoadFailed) return;
    setChannelAction('disable');
    setChannelError('');
    try {
      await disableWhatsAppChannelBinding(binding.id);
      setBinding(null);
      setBaseUrl('');
      setApiKey('');
      showToast('WhatsApp disconnected from this workspace', 'success');
      onBindingChange?.();
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

  const finishPairing = async () => {
    setStep('idle');
    setPhone('');
    setLinkHint('');
    await loadIdentities();
    onChange?.();
  };

  const whatsappIdentities = identities.filter((i) => i.channel === 'whatsapp');

  const title = WHATSAPP_CONNECTOR.name;
  const busy = channelAction !== null || submitting;
  const closeDialog = () => { if (!busy) dialog?.onClose(); };

  function renderSettings(body: ReactNode, primary?: ReactNode) {
    if (dialog) {
      return (
        <FormDialog open title={`${dialog.mode} ${title}`} onClose={closeDialog}
          actions={<>
            <Button type="button" variant="ghost" size="sm" onClick={closeDialog} disabled={busy}>
              {dialog.mode === 'Add' ? 'Cancel' : 'Done'}
            </Button>
            {primary}
          </>}>
          {body}
        </FormDialog>
      );
    }
    return (
      <div className="app-card p-5">
        <Text as="h3" variant="heading-sm" className="mb-4 flex items-center gap-2">
          <IconWell size="sm" aria-hidden><Phone size={16} strokeWidth={LINE_ICON_STROKE} /></IconWell>
          {title}
        </Text>
        {body}
      </div>
    );
  }

  if (!enabled) return renderSettings(
    <Text variant="body" tone="muted" as="p">
      WhatsApp is unavailable. Contact your administrator to check channel configuration.
    </Text>,
  );
  if (loading || !bindingReady) return renderSettings(
    <div role="status" className="flex items-center gap-3">
      <Loader2 size={18} className="animate-spin text-[var(--text-muted)]" aria-hidden />
      <Text variant="body-sm" tone="muted" as="span">Loading WhatsApp settings…</Text>
    </div>,
  );
  if (loadError) return renderSettings(
    <div role="alert">
      <Text as="p" variant="body-sm" tone="danger">Could not load WhatsApp numbers.</Text>
      <Button variant="outline" size="sm" onClick={() => { void loadIdentities(); void loadBinding(); }}>Try again</Button>
    </div>,
  );

  const canEditWorkspace = canManageLine && !bindingLoadFailed && dialog?.mode !== 'View';
  const callingEnabled = Boolean(binding?.calling_enabled);

  const handleCalling = async (enabled: boolean) => {
    if (!canEditWorkspace || !binding?.calling_grant_id || busy) return;
    setChannelAction('calling');
    try {
      await setWhatsAppCalling({
        calling_enabled: enabled,
        expected_grant_id: binding.calling_grant_id,
        expected_revision: binding.calling_revision ?? 0,
      });
      await loadBinding();
    } catch (error: unknown) {
      const status = typeof error === 'object' && error !== null && 'response' in error
        ? (error as { response?: { status?: number } }).response?.status
        : undefined;
      showToast(responseMessage(error, 'Could not update voice calling'), 'error');
      if (status === 409 || status === 403 || status === 503) await loadBinding();
    } finally {
      setChannelAction(null);
    }
  };

  const handleVoiceService = async () => {
    if (!canEditWorkspace || !binding || busy) return;
    setChannelAction('voice');
    setChannelError('');
    try {
      const saved = await saveWhatsAppVoiceService({
        jvvoice_base_url: voiceUrl.trim(),
        api_key: voiceKey,
      });
      setVoiceKey('');
      if (saved?.binding) {
        const next = withoutSecrets(saved.binding);
        setBinding(next);
        setVoiceUrl(next.jvvoice_base_url || voiceUrl.trim());
      }
      showToast('Voice service saved', 'success');
      await loadBinding();
    } catch (error: unknown) {
      const message = responseMessage(error, 'Could not save voice service');
      setChannelError(message);
      showToast(message, 'error');
    } finally {
      setChannelAction(null);
    }
  };

  const primary = canEditWorkspace ? (
    <Button variant="primary" size="sm" onClick={() => void handleSaveChannel()}
      loading={channelAction === 'save'} disabled={busy || (Boolean(dialog) && (!baseUrl.trim() || !apiKey.trim()))}>
      {binding ? 'Save changes' : 'Add connection'}
    </Button>
  ) : null;

  const personalSettings = (
    <section className="space-y-3" aria-label="Personal WhatsApp number">
      <Text as="h4" variant="heading-sm">Personal WhatsApp number</Text>
      <Text as="p" variant="body-sm" tone="muted">Link your number to chat with Integral on WhatsApp.</Text>

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
          <Button variant="ghost" size="sm" onClick={() => void finishPairing()}>
            Done
          </Button>
        </div>
      )}
    </section>
  );

  const workspaceSettings = (
    <section className="space-y-3" aria-label="Workspace WhatsApp line">
      <Text variant="heading-sm" as="h4">Workspace WhatsApp line</Text>
      {!binding && !bindingLoadFailed && <Text variant="body-sm" tone="muted" as="p">
        No WhatsApp line is connected to this workspace.
      </Text>}
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
      {binding && (
        <div className="space-y-3">
          <ToggleRow
            label="Voice calling"
            hint="Text messaging stays available when calling is off. This also turns calling on or off in JV Connect."
            checked={callingEnabled}
            disabled={!canEditWorkspace || !binding.calling_grant_id || busy}
            onChange={(enabled) => { void handleCalling(enabled); }}
          />
          {callingEnabled && (
            <div className="space-y-3 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--panel-2)] p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Text as="h5" variant="heading-sm">Voice service</Text>
                {binding.jvconnect_calling_status === 'ENABLED' ? (
                  <StatusPill state="ok">JV Connect calling on</StatusPill>
                ) : binding.jvconnect_calling_status === 'DISABLED' ? (
                  <StatusPill state="warn">JV Connect calling off</StatusPill>
                ) : binding.jvconnect_calling_error ? (
                  <StatusPill state="warn">JV Connect calling settings unavailable</StatusPill>
                ) : null}
              </div>
              <Text as="p" variant="body-sm" tone="muted">
                Integral accepts calls through jvvoice. LiveKit URL and keys stay on that jvvoice host.
              </Text>
              {binding.jvconnect_calling_error ? (
                <Text as="p" variant="body-sm" tone="danger">{binding.jvconnect_calling_error}</Text>
              ) : null}
              <Field label="jvvoice base URL">
                <Input
                  type="url"
                  value={voiceUrl}
                  autoComplete="off"
                  disabled={!canEditWorkspace || busy}
                  onChange={(event) => setVoiceUrl(event.target.value)}
                  placeholder="https://jvvoice.example"
                />
              </Field>
              <Field label="jvvoice API key">
                <Input
                  type="password"
                  value={voiceKey}
                  autoComplete="new-password"
                  disabled={!canEditWorkspace || busy}
                  onChange={(event) => setVoiceKey(event.target.value)}
                  placeholder={binding.jvvoice_api_key_last4 ? `Saved key ending in ${binding.jvvoice_api_key_last4}` : 'Optional if the server already has a key'}
                />
              </Field>
              {canEditWorkspace && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void handleVoiceService()}
                  loading={channelAction === 'voice'}
                  disabled={busy}
                >
                  Save voice service
                </Button>
              )}
            </div>
          )}
        </div>
      )}
      {channelError ? <Text variant="body-sm" tone="danger" as="p" role="alert">{channelError}</Text> : null}
      {bindingLoadFailed && <Button variant="outline" size="sm" onClick={() => void loadBinding()}>Retry connection status</Button>}
      {!canManageLine && <Text variant="body-sm" tone="muted" as="p">
        Workspace admins and owners manage the workspace line.
      </Text>}
      {canEditWorkspace && <>
        <Field label="jvconnect base URL">
          <Input type="text" value={baseUrl} autoComplete="off" onChange={(event) => setBaseUrl(event.target.value)} />
        </Field>
        <Field label="API key">
          <Input type="password" value={apiKey} autoComplete="new-password" onChange={(event) => setApiKey(event.target.value)} />
        </Field>
        {(!dialog || binding) && <div className="flex gap-2">
          {!dialog && primary}
          {binding && (
            <>
              <Button variant="outline" size="sm" onClick={() => void handleReregisterWebhook()} loading={channelAction === 'webhook'} disabled={busy}>
                Reregister webhook
              </Button>
              <Button variant="outline" size="sm" onClick={() => void handleDisableChannel()} loading={channelAction === 'disable'} disabled={busy}>
                Disconnect
              </Button>
            </>
          )}
        </div>}
      </>}
    </section>
  );

  return renderSettings(
    <div className="flex flex-col gap-4">
      {workspaceSettings}
      <div className="border-t border-[var(--panel-border)] pt-4">{personalSettings}</div>
    </div>,
    primary,
  );
}
