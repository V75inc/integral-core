/**
 * Post-install App settings editor — GET/PATCH /api/apps/{id}/settings.
 */
import { useEffect, useState } from 'react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui';
import { Text } from '../../ui';
import { AppSettingsForm } from './AppSettingsForm';
import { appsApi } from '../../api/apps';
import { errorMessageFromAxios } from '../../api/helpers';

export function appHasConfigurableSettings(
  schema: Record<string, unknown> | undefined,
): boolean {
  const properties = (schema?.properties || {}) as Record<string, unknown>;
  return Object.keys(properties).length > 0;
}

interface AppSettingsModalProps {
  appId: string;
  appName: string;
  open: boolean;
  onClose(): void;
  onSaved?(): void;
}

export function AppSettingsModal({
  appId,
  appName,
  open,
  onClose,
  onSaved,
}: AppSettingsModalProps) {
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [schema, setSchema] = useState<Record<string, unknown>>({});
  const [value, setValue] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (!open || !appId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void appsApi
      .getAppSettings(appId)
      .then(res => {
        if (cancelled) return;
        setSchema(res.settings_schema || {});
        setValue(res.settings || {});
      })
      .catch(err => {
        if (cancelled) return;
        setError(errorMessageFromAxios(err, 'Could not load app settings'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, appId]);

  async function handleSave() {
    setSubmitting(true);
    setError(null);
    try {
      const res = await appsApi.patchAppSettings(appId, value);
      setValue(res.settings || {});
      onSaved?.();
      onClose();
    } catch (err) {
      setError(errorMessageFromAxios(err, 'Could not save app settings'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={`${appName} settings`} width="max-w-dialog-wide">
      <Modal.Body>
        {loading ? (
          <Text variant="body-sm" tone="subtle" as="p">
            Loading settings…
          </Text>
        ) : appHasConfigurableSettings(schema) ? (
          <>
            <Text variant="body-sm" tone="subtle" as="p" className="mb-4">
              Provider credentials and delivery options for this app. Secret fields
              are write-only — leave blank to keep the current key.
            </Text>
            <AppSettingsForm
              schema={schema}
              value={value}
              onChange={setValue}
              disabled={submitting}
            />
            {error ? (
              <Text variant="body-sm" tone="danger" as="p" className="mt-3" data-testid="settings-error">
                {error}
              </Text>
            ) : null}
          </>
        ) : (
          <Text variant="body-sm" tone="subtle" as="p">
            This app has no configurable settings.
          </Text>
        )}
      </Modal.Body>
      <Modal.Footer align="end">
        <Button variant="ghost" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={handleSave}
          loading={submitting}
          disabled={loading || !appHasConfigurableSettings(schema)}
          data-testid="app-settings-save"
        >
          Save settings
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
