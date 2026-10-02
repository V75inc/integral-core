/**
 * Post-install App settings editor — PATCH /apps/{id}/settings.
 *
 * Install-time settings use AppSettingsFinalizeStep; this modal is the
 * Settings page for an already-active App (invoice patterns, Email Log delivery, etc.).
 */
import { useEffect, useState } from 'react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui';
import { Text } from '../../ui';
import { AppSettingsForm } from './AppSettingsForm';
import { appsApi } from '../../api/apps';
import { errorMessageFromAxios } from '../../api/helpers';

function seedDefaultsFromSchema(
  schema: Record<string, unknown>,
): Record<string, unknown> {
  const properties = (schema?.properties || {}) as Record<
    string,
    Record<string, unknown>
  >;
  const required = (schema?.required as string[] | undefined) || [];
  const seeded: Record<string, unknown> = {};
  for (const [key, prop] of Object.entries(properties)) {
    if (Object.prototype.hasOwnProperty.call(prop, 'default')) {
      seeded[key] = prop.default;
      continue;
    }
    const enumVals = prop.enum as unknown[] | undefined;
    const isArray = prop.type === 'array';
    if (required.includes(key) && !isArray && enumVals && enumVals.length) {
      seeded[key] = enumVals[0];
    }
  }
  return seeded;
}

export function appHasConfigurableSettings(
  schema: Record<string, unknown> | undefined,
): boolean {
  const properties = (schema?.properties || {}) as Record<string, unknown>;
  return Object.keys(properties).length > 0;
}

export interface AppSettingsModalProps {
  open: boolean;
  appId: string;
  appName: string;
  onClose(): void;
  onSaved?(): void;
}

export function AppSettingsModal({
  open,
  appId,
  appName,
  onClose,
  onSaved,
}: AppSettingsModalProps) {
  const [schema, setSchema] = useState<Record<string, unknown> | null>(null);
  const [value, setValue] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !appId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSchema(null);
    void appsApi
      .getAppSettings(appId)
      .then(res => {
        if (cancelled) return;
        const nextSchema = res.settings_schema || {};
        setSchema(nextSchema);
        setValue({
          ...seedDefaultsFromSchema(nextSchema),
          ...(res.settings || {}),
        });
      })
      .catch(err => {
        if (cancelled) return;
        setError(errorMessageFromAxios(err, 'Failed to load settings'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, appId]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const res = await appsApi.updateAppSettings(appId, value);
      setValue(res.settings || value);
      onSaved?.();
      onClose();
    } catch (err) {
      setError(errorMessageFromAxios(err, 'Failed to save settings'));
    } finally {
      setSaving(false);
    }
  }

  const schemaRecord = schema || {};

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`${appName} settings`}
      width="max-w-dialog-wide"
    >
      <Modal.Body>
        {loading ? (
          <Text variant="body-sm" tone="subtle" as="p">
            Loading settings…
          </Text>
        ) : appHasConfigurableSettings(schemaRecord) ? (
          <>
            <Text variant="body-sm" tone="subtle" as="p" className="mb-4">
              Provider credentials and delivery options for this app. Secret fields
              are write-only — leave blank to keep the current key.
            </Text>
            <AppSettingsForm
              schema={schemaRecord}
              value={value}
              onChange={setValue}
              disabled={saving}
            />
          </>
        ) : schema ? (
          <Text variant="body-sm" tone="subtle" as="p">
            This app has no configurable settings.
          </Text>
        ) : null}
        {error ? (
          <Text
            variant="body-sm"
            tone="danger"
            as="p"
            className="mt-3"
            data-testid="app-settings-modal-error"
          >
            {error}
          </Text>
        ) : null}
      </Modal.Body>
      <Modal.Footer align="end">
        <Button variant="ghost" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={handleSave}
          loading={saving}
          disabled={loading || !appHasConfigurableSettings(schemaRecord)}
          data-testid="app-settings-modal-save"
        >
          Save settings
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
