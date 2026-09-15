import { useEffect, useRef, useState } from 'react';

import {
  MCP_OAUTH_MESSAGE_TYPE,
  connectorsApi,
  type CatalogAuthField,
  type CatalogEntry,
  type ConnectorResponse,
} from '../../../api/connectors';
import { Button } from '../../../components/ui/Button';
import { Modal } from '../../../components/ui/Modal';
import { Text } from '../../../ui';
import { useToast } from '../../../context/ToastContext';
import { SettingsField, TextInput, ToggleRow } from '../components/Field';

function isToggleField(field: CatalogAuthField): boolean {
  return (
    field.control === 'toggle' || field.name.startsWith('QUICKBOOKS_DISABLE_')
  );
}

function toggleLabel(field: CatalogAuthField): string {
  return field.label.replace(/\s*\(true\/false\)\s*$/i, '').trim() || field.label;
}

function seedSecrets(fields: CatalogAuthField[]): Record<string, string> {
  const seeded: Record<string, string> = {};
  for (const field of fields) {
    if (!isToggleField(field)) continue;
    seeded[field.name] = field.default === 'true' ? 'true' : 'false';
  }
  return seeded;
}

function toggleOn(
  field: CatalogAuthField,
  secrets: Record<string, string>,
): boolean {
  const value = secrets[field.name];
  if (value === 'true') return true;
  if (value === 'false') return false;
  return field.default === 'true';
}

export function ConnectorInstallSheet({
  entry,
  onClose,
  onInstalled,
}: {
  entry: CatalogEntry;
  onClose: () => void;
  onInstalled: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fields = entry.auth.fields ?? [];
  const [secrets, setSecrets] = useState<Record<string, string>>(() =>
    seedSecrets(fields),
  );
  const [oauthOpened, setOauthOpened] = useState(false);
  const popupRef = useRef<Window | null>(null);

  // Watch for the user closing the popup without finishing. Without this the
  // sheet sat on a "Done" button that called onInstalled() unconditionally —
  // abandoning the consent screen reported the connector as connected.
  useEffect(() => {
    if (!oauthOpened) return;
    const timer = window.setInterval(() => {
      if (popupRef.current && popupRef.current.closed) {
        window.clearInterval(timer);
        popupRef.current = null;
        setOauthOpened(false);
        const message = 'Authorization was not completed.';
        setError(message);
        toast.showToast(message, 'error');
      }
    }, 700);
    return () => window.clearInterval(timer);
  }, [oauthOpened, toast]);

  useEffect(() => {
    if (!oauthOpened) return;
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as {
        type?: string;
        ok?: boolean;
        connector?: ConnectorResponse;
        error?: string;
      };
      if (!data || data.type !== MCP_OAUTH_MESSAGE_TYPE) return;
      if (data.ok) {
        // Clear the handle first: closing the popup after a success must not
        // trip the abandonment watcher above.
        popupRef.current = null;
        setOauthOpened(false);
        toast.showToast(`${entry.display_name} connected`, 'success');
        onInstalled();
        return;
      }
      const message = data.error || 'Authorization was cancelled';
      setError(message);
      setOauthOpened(false);
      toast.showToast(message, 'error');
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [oauthOpened, entry.display_name, onInstalled, toast]);

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await connectorsApi.installFromCatalog(entry.slug, { secrets });
      if (result.action === 'oauth' && result.consent_url) {
        const popup = window.open(
          result.consent_url,
          `${entry.slug}-oauth`,
          'width=600,height=720,scrollbars=yes',
        );
        if (!popup) {
          const message =
            'Popup blocked. Allow popups to authorize this connector.';
          setError(message);
          toast.showToast(message, 'error');
          return;
        }
        popupRef.current = popup;
        setOauthOpened(true);
        toast.showToast(`Authorize ${entry.display_name} in the popup`, 'success');
        return;
      }
      toast.showToast(`${entry.display_name} connected`, 'success');
      onInstalled();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Install failed';
      setError(message);
      toast.showToast(message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const authLabel =
    entry.auth.type === 'oauth2'
      ? 'OAuth'
      : entry.auth.type === 'headers'
        ? 'API headers'
        : entry.auth.type === 'api_key'
          ? 'API key'
          : entry.auth.type === 'env'
            ? 'Configuration'
            : 'No extra credentials';

  return (
    <Modal
      open
      onClose={onClose}
      title={`Install ${entry.display_name}`}
      variant="compact"
    >
      <Modal.Body>
        <Text variant="body-sm" tone="subtle" as="p">
          {entry.description}
        </Text>
        <Text variant="body-sm" tone="muted" as="p">
          Auth: {authLabel}
        </Text>
        {entry.auth.type === 'oauth2' ? (
          <Text variant="body-sm" tone="subtle" as="p">
            Paste Client ID and Client secret from the app&apos;s developer
            console. Leave a field blank to use the matching server
            environment variable.
          </Text>
        ) : null}
        {entry.kind === 'mcp' && entry.auth.type === 'oauth2' ? (
          <Text variant="body-sm" tone="subtle" as="p">
            {entry.transport === 'stdio'
              ? 'Connect opens Intuit sign-in. Use the same QuickBooks OAuth app and redirect URI as native QuickBooks. All tools are available; turn a restriction on to hide create, update, or delete.'
              : "Connect opens a sign-in popup. Register this app's MCP OAuth callback URL on the OAuth client."}
          </Text>
        ) : null}
        {entry.kind === 'mcp' && entry.auth.type === 'none' ? (
          <Text variant="body-sm" tone="subtle" as="p">
            If the server requires sign-in, a consent popup will open after
            you install.
          </Text>
        ) : null}

        {oauthOpened ? (
          <>
            <Text variant="body-sm" as="p">
              Complete sign-in in the popup. This window will continue once
              authorization finishes.
            </Text>
            {error ? (
              <Text variant="body-sm" tone="danger" as="p">
                {error}
              </Text>
            ) : null}
          </>
        ) : (
          <>
            {fields.map(field =>
              isToggleField(field) ? (
                <ToggleRow
                  key={field.name}
                  checked={toggleOn(field, secrets)}
                  onChange={on =>
                    setSecrets(prev => ({
                      ...prev,
                      [field.name]: on ? 'true' : 'false',
                    }))
                  }
                  label={toggleLabel(field)}
                  hint={field.hint || undefined}
                />
              ) : (
                <SettingsField key={field.name} label={field.label}>
                  <TextInput
                    type={field.secret ? 'password' : 'text'}
                    value={secrets[field.name] ?? ''}
                    onChange={v =>
                      setSecrets(prev => ({ ...prev, [field.name]: v }))
                    }
                    placeholder={field.required ? 'Required' : 'Optional'}
                  />
                </SettingsField>
              ),
            )}
            {error ? (
              <Text variant="body-sm" tone="danger" as="p">
                {error}
              </Text>
            ) : null}
          </>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        {oauthOpened ? (
          <Button type="button" variant="primary" size="sm" disabled>
            Waiting for authorization…
          </Button>
        ) : (
          <Button
            type="button"
            variant="primary"
            size="sm"
            disabled={busy}
            onClick={() => void submit()}
          >
            {entry.auth.type === 'oauth2' || entry.kind === 'mcp'
              ? 'Connect'
              : 'Install'}
          </Button>
        )}
      </Modal.Footer>
    </Modal>
  );
}
