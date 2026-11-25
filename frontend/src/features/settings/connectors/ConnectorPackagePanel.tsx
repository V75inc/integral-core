import type { ConnectorBindingScope } from '../../../api/connectorPackages';
import { useConfirm } from '../../../context/ConfirmContext';
import { connectorRemoval } from './connectorRemoval';
import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import {
  disableWhatsAppChannelBinding,
  getWhatsAppChannelBinding,
  saveWhatsAppChannelBinding,
} from '../../../api/agentive';
import {
  connectorPackagesApi,
  type ConnectorPackageField,
  type ConnectorPackageInstallation,
  type ConnectorPackageSummary,
} from '../../../api/connectorPackages';
import { connectorsApi } from '../../../api/connectors';
import { managedChannelsApi } from '../../../api/managedChannels';
import { AppSettingsForm } from '../../../components/apps/AppSettingsForm';
import { ConnectorBindingsPanel } from '../sections/ConnectorBindingsPanel';
import type { JsonSchema } from '../../../components/apps/widgets';
import { Button } from '../../../components/ui/Button';
import { Input, Select, Surface, Text } from '../../../ui';
import { Modal } from '../../../components/ui/Modal';

type InstallationTargets = Partial<Record<ConnectorBindingScope, string>>;

export function ConnectorPackageDetail({
  workspaceId, pkg, installations = [], initialInstallationId, onClose, onChanged, onManageChannel, targets, onInstalledInScope,
}: {
  workspaceId: string;
  pkg?: ConnectorPackageSummary;
  installations?: ConnectorPackageInstallation[];
  initialInstallationId?: string;
  onClose: () => void;
  onChanged: () => void;
  onManageChannel?: () => void;
  targets?: InstallationTargets;
  onInstalledInScope?: (target: string, installation: ConnectorPackageInstallation) => void;
}) {
  const [selectedId, setSelectedId] = useState(initialInstallationId);
  const [adding, setAdding] = useState(false);
  const row = installations.find(installation => installation.id === selectedId) ?? installations[0];
  const name = pkg?.name ?? row?.name;
  if (!name) return null;
  const configured = !adding && Boolean(row || pkg?.grant?.present);
  return (
    <Modal open title={`${configured ? 'Configure' : 'Add'} ${name}`} onClose={onClose}>
      <Modal.Body>
        <div className="flex flex-col gap-3">
          {pkg && <Text as="p" tone="muted">{pkg.description}</Text>}
          {installations.length > 1 && (
            <label>
              Installation
              <Select aria-label="Installation" value={row?.id} onChange={event => setSelectedId(event.target.value)}>
                {installations.map((installation, index) => (
                  <option key={installation.id} value={installation.id}>
                    {installation.name} {index + 1} · {installation.enabled ? 'Enabled' : 'Disabled'}
                  </option>
                ))}
              </Select>
            </label>
          )}
          {row && !adding ? (
            <InstallationCard key={row.id} row={row} workspaceId={workspaceId}
              fields={(pkg?.configuration ?? []).filter(field => !field.scope || field.scope ===
                (row.connection_mode === 'per_user' ? 'personal' : 'workspace'))} oauth={Boolean(pkg?.auth_type)}
              sync={pkg?.capabilities.includes('sync') ?? false}
              onChanged={onChanged} onRemoved={() => { onChanged(); if (installations.length === 1) onClose(); }} />
          ) : pkg?.host_managed ? (
            <HostManagedCard pkg={pkg} workspaceId={workspaceId} onChanged={onChanged}
              canDisconnectFromCard={pkg.channel_mode !== 'managed-text-v1'} />
          ) : pkg ? (
            <PackageCard pkg={pkg} targets={targets ?? { workspace: workspaceId }}
              onInstalled={(target, installation) => {
                setAdding(false);
                setSelectedId(installation.id);
                if (onInstalledInScope) onInstalledInScope(target, installation);
                else onChanged();
              }} />
          ) : null}
        </div>
      </Modal.Body>
      <Modal.Footer>
        {row && !adding && !initialInstallationId && pkg && !pkg.host_managed &&
          pkg.binding_scopes?.some(binding => targets?.[binding] && targets[binding] !== workspaceId) &&
          <Button type="button" variant="secondary" onClick={() => setAdding(true)}>Add in another scope</Button>}
        {onManageChannel && <Button type="button" variant="secondary" onClick={onManageChannel}>
          Channel status and controls
        </Button>}
        <Button type="button" variant="ghost" onClick={onClose}>Close</Button>
      </Modal.Footer>
    </Modal>
  );
}

export function configurationSchema(
  fields: ConnectorPackageField[],
  requireSecrets: boolean,
  resolved: Record<string, { value: string; label: string }[]> = {},
): JsonSchema {
  const properties: Record<string, JsonSchema> = {};
  const required: string[] = [];
  for (const field of fields) {
    const property: JsonSchema = { title: field.label, description: field.help };
    if (field.type === 'number') property.type = 'number';
    else if (field.type === 'boolean') property.type = 'boolean';
    else if (field.type === 'text') {
      property.type = 'string';
      property['ui:widget'] = 'textarea';
    } else if (field.type === 'secret') {
      property.type = 'string';
      property['ui:widget'] = 'secret';
    } else if (field.type === 'select') {
      const options = field.options_from ? (resolved[field.key] ?? []) : (field.options ?? []);
      const labels = Object.fromEntries(options.map(option => [option.value, option.label]));
      property['x-option-labels'] = labels;
      if (field.multiple) {
        property.type = 'array';
        property.items = { type: 'string', enum: options.map(option => option.value) };
        property['ui:widget'] = 'multi_select';
      } else {
        property.type = 'string';
        property.enum = options.map(option => option.value);
        property['ui:widget'] = 'select';
      }
    } else property.type = 'string';
    properties[field.key] = property;
    if (field.required && (requireSecrets || field.type !== 'secret')) required.push(field.key);
  }
  return { type: 'object', properties, required };
}

function HostManagedCard({
  pkg,
  workspaceId,
  onChanged,
  canDisconnectFromCard,
}: {
  pkg: ConnectorPackageSummary;
  workspaceId: string;
  onChanged: () => void;
  canDisconnectFromCard: boolean;
}) {
  const grant = pkg.grant;
  const hasLineGrant = grant != null;
  const bindingQuery = useQuery({
    queryKey: ['connectors', 'host-line', pkg.id],
    queryFn: () => canDisconnectFromCard
      ? getWhatsAppChannelBinding()
      : managedChannelsApi.credentialMetadata(workspaceId, pkg.id),
    enabled: hasLineGrant,
  });
  const binding = bindingQuery.data?.binding ?? null;
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  useEffect(() => {
    if (binding?.jvconnect_base_url) setBaseUrl(binding.jvconnect_base_url);
  }, [binding?.jvconnect_base_url]);
  const saveLine = useMutation({
    mutationFn: async () => {
      const values = {
        jvconnect_base_url: baseUrl.trim(),
        api_key: apiKey,
        expected_binding_id: binding?.id ?? grant?.id ?? null,
      };
      if (canDisconnectFromCard) {
        await saveWhatsAppChannelBinding(values);
      } else {
        await managedChannelsApi.configureCredentials(workspaceId, pkg.id, values);
      }
    },
    onSuccess: () => {
      setApiKey('');
      onChanged();
    },
  });
  const disconnect = useMutation({
    mutationFn: () => disableWhatsAppChannelBinding(grant?.id || ''),
    onSuccess: onChanged,
  });
  const canDisconnect = canDisconnectFromCard && Boolean(grant?.present && grant?.id);
  return (
    <Surface border="subtle" radius="card" padding="md" className="mt-3 flex flex-col gap-2">
      {grant?.present ? (
        <Text as="p" variant="body-sm">
          {grant.enabled ? 'Enabled' : 'Disabled'} · {grant.edge}
        </Text>
      ) : (
        <Text as="p" variant="body-sm" tone="muted">No grant on this workspace.</Text>
      )}
      {hasLineGrant ? (
        <>
          <label className="flex flex-col gap-1">
            <Text as="span" variant="body-sm">jvconnect base URL</Text>
            <Input
              value={baseUrl}
              autoComplete="off"
              onChange={event => setBaseUrl(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <Text as="span" variant="body-sm">API key</Text>
            <Input
              type="password"
              value={apiKey}
              autoComplete="new-password"
              onChange={event => setApiKey(event.target.value)}
            />
          </label>
          <Button
            type="button"
            size="sm"
            onClick={() => saveLine.mutate()}
            disabled={!baseUrl.trim() || !apiKey.trim()}
          >
            Save line
          </Button>
        </>
      ) : null}
      {canDisconnect ? (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => disconnect.mutate()}
        >
          Disconnect
        </Button>
      ) : null}
    </Surface>
  );
}

function PackageCard({ pkg, targets, onInstalled }: {
  pkg: ConnectorPackageSummary;
  targets: InstallationTargets;
  onInstalled: (target: string, installation: ConnectorPackageInstallation) => void;
}) {
  const allowed = (pkg.binding_scopes ?? ['workspace']).filter(binding => targets[binding]);
  const [choice, setChoice] = useState<ConnectorBindingScope>();
  const selected = allowed.length === 1 ? allowed[0] : choice;
  const target = selected ? targets[selected] : undefined;
  const [values, setValues] = useState<Record<string, unknown>>({});
  const install = useMutation({
    mutationFn: async () => {
      if (!target || !selected) throw new Error('Choose an installation scope.');
      return { target, installation: await connectorPackagesApi.install(target, pkg.id, values, selected) };
    },
    onSuccess: result => onInstalled(result.target, result.installation),
  });
  return (
    <Surface border="subtle" radius="card" padding="md" className="mt-3 flex flex-col gap-3">
      {allowed.length > 1 && <label>
        Installation scope
        <Select aria-label="Installation scope" value={choice ?? ''} disabled={install.isPending}
          onChange={event => {
            const binding = allowed.find(option => option === event.target.value);
            setChoice(binding);
            setValues({});
          }}>
          <option value="">Choose a scope</option>
          {allowed.map(binding => <option key={binding} value={binding}>
            {binding === 'personal' ? 'Personal Workspace' : 'This workspace'}
          </option>)}
        </Select>
      </label>}
      {selected && target && <AppSettingsForm
        schema={configurationSchema(pkg.configuration.filter(field => !field.scope || field.scope === selected), true)}
        value={values} onChange={setValues} idPrefix={pkg.id}
      />}
      {allowed.length === 0 && <Text as="p" tone="muted">This connector is unavailable in the scopes you can manage.</Text>}
      {install.isError && <Text as="p" role="alert" tone="danger">{install.error.message}</Text>}
      <Button type="button" size="sm" disabled={!target || install.isPending} onClick={() => install.mutate()}>Add</Button>
    </Surface>
  );
}

function readOptionPath(value: unknown, path: string): unknown {
  if (path === '$') return value;
  return path.replace(/^\$\./, '').split('.').reduce<unknown>((current, key) =>
    current !== null && typeof current === 'object'
      ? (current as Record<string, unknown>)[key]
      : undefined,
  value);
}

function InstallationCard({
  workspaceId,
  row,
  fields,
  sync,
  oauth,
  onChanged,
  onRemoved,
}: {
  workspaceId: string;
  row: ConnectorPackageInstallation;
  fields: ConnectorPackageField[];
  sync: boolean;
  oauth: boolean;
  onChanged: () => void;
  onRemoved: () => void;
}) {
  const confirm = useConfirm();
  const available = row.package_available !== false;
  const [values, setValues] = useState<Record<string, unknown>>({ ...row.config });
  const options = useQuery({
    queryKey: ['connectors', 'package-options', workspaceId, row.id, row.revision],
    enabled: available && fields.some(field => field.options_from),
    queryFn: async () => {
      const entries = await Promise.all(fields.filter(field => field.options_from).map(async field => {
        const source = field.options_from!;
        const { body } = await connectorsApi.readAction(row.id, source.action);
        const items = readOptionPath(body, source.items);
        const choices = Array.isArray(items) ? items.flatMap(item => {
          const value = readOptionPath(item, source.value);
          const label = readOptionPath(item, source.label);
          return typeof value === 'string' && typeof label === 'string' ? [{ value, label }] : [];
        }) : [];
        return [field.key, choices] as const;
      }));
      return Object.fromEntries(entries);
    },
  });
  const authorize = useMutation({
    mutationFn: async () => {
      const popup = window.open('about:blank', '_blank', 'width=600,height=720');
      if (!popup) throw new Error('Allow popups to authorize this connector.');
      try {
        const result = await connectorPackagesApi.startOAuth(workspaceId, row.package_id, row.id);
        popup.location.replace(result.consent_url);
      } catch (error) {
        popup.close();
        throw error;
      }
    },
  });
  const configure = useMutation({
    mutationFn: () => {
      const payload = { ...values };
      for (const field of fields) {
        if (field.type === 'secret' && payload[field.key] === '') {
          delete payload[field.key];
        }
      }
      return connectorPackagesApi.configure(workspaceId, row.id, payload);
    },
    onSuccess: onChanged,
  });
  const lifecycle = useMutation({
    mutationFn: async (action: 'enable' | 'disable' | 'upgrade' | 'rollback' | 'remove') => {
      if (action === 'upgrade') await connectorPackagesApi.upgrade(workspaceId, row.id);
      else if (action === 'rollback') await connectorPackagesApi.rollback(workspaceId, row.id);
      else if (action === 'remove') await connectorPackagesApi.remove(workspaceId, row.id);
      else await connectorPackagesApi.setEnabled(workspaceId, row.id, action === 'enable');
    },
    onSuccess: (_, action) => action === 'remove' ? onRemoved() : onChanged(),
  });
  const error = configure.error ?? authorize.error ?? lifecycle.error;
  return (
    <Surface border="subtle" radius="card" padding="md" className="mt-3 flex flex-col gap-2">
      <Text as="p" variant="body-sm">
        {row.name} · {row.enabled ? 'Enabled' : 'Disabled'} · {row.health_status}
        {available ? '' : ' · Package unavailable'}
      </Text>
      {available && fields.length > 0 ? (
        <>
          <AppSettingsForm
            schema={configurationSchema(fields, false, options.data)}
            value={values}
            onChange={setValues}
            idPrefix={row.id}
          />
          {fields.filter(field => field.type === 'secret').map(field => (
            <Button
              key={field.key}
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setValues(current => ({ ...current, [field.key]: null }))}
            >
              {`Remove ${field.label}`}
            </Button>
          ))}
          <Button type="button" size="sm" onClick={() => configure.mutate()}>Save</Button>
        </>
      ) : null}
      {error && <Text as="p" role="alert" variant="body-sm" tone="danger">{error.message}</Text>}
      <div className="flex gap-2">
        {available && oauth && (
          <Button type="button" size="sm" variant="secondary" onClick={() => authorize.mutate()}>
            Authorize
          </Button>
        )}
        {available && row.upgrade_available ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("upgrade")}
          >
            Upgrade
          </Button>
        ) : null}
        {row.rollback_available ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("rollback")}
          >
            Roll back
          </Button>
        ) : null}
        {available || row.enabled ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={lifecycle.isPending} onClick={() => lifecycle.mutate(row.enabled ? "disable" : "enable")}
          >
            {row.enabled ? 'Disable' : 'Enable'}
          </Button>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={lifecycle.isPending}
          onClick={async () => {
            if (await confirm(connectorRemoval(`${row.name} · ${row.id}`))) lifecycle.mutate("remove");
          }}
        >
          Remove
        </Button>
      </div>
      {sync ? <ConnectorBindingsPanel connectorId={row.id} /> : null}
    </Surface>
  );
}
