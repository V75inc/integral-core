import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

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
import { Input, Surface, Text } from '../../../ui';
import { SettingsSection } from '../components/Field';

/**
 * Renders every admitted drop-in package from the manifest. No package id is special-cased.
 */
export function ConnectorPackagePanel({
  workspaceId,
  managedSlugs = [],
}: { workspaceId: string; managedSlugs?: string[] }) {
  const queryClient = useQueryClient();
  const queryKey = ['connectors', 'packages', workspaceId] as const;
  const catalogue = useQuery({
    queryKey,
    queryFn: () => connectorPackagesApi.list(workspaceId),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['connectors', 'managed-channels'] });
    return queryClient.invalidateQueries({ queryKey });
  };
  const rescan = useMutation({
    mutationFn: () => connectorPackagesApi.rescan(workspaceId),
    onSuccess: () => refresh(),
  });
  const packages = catalogue.data?.packages ?? [];
  const visiblePackages = packages.filter(pkg =>
    !pkg.host_managed || pkg.channel_mode !== 'managed-text-v1' || pkg.grant,
  );
  const fieldsByPackage = new Map(packages.map(pkg => [pkg.id, pkg.configuration]));

  return (
    <SettingsSection
      title="Dropped-in packages"
      description="Packages found in the connector folder. Invalid ones are listed with the reason they were not offered."
    >
      <div className="mb-3">
        <Button type="button" variant="secondary" size="sm" onClick={() => rescan.mutate()}>
          Rescan
        </Button>
      </div>
      {(catalogue.data?.rejected ?? []).map(row => (
        <Text key={row.id} as="p" variant="body-sm" tone="muted">
          {row.id}: {row.reason}
        </Text>
      ))}
      {visiblePackages.map(pkg => (
        pkg.host_managed ? (
          <HostManagedCard key={pkg.id} pkg={pkg} workspaceId={workspaceId} onChanged={refresh} canDisconnectFromCard={!managedSlugs.includes(pkg.id) && pkg.channel_mode !== 'managed-text-v1'} />
        ) : (
          <PackageCard key={pkg.id} workspaceId={workspaceId} pkg={pkg} onChanged={refresh} />
        )
      ))}
      {(catalogue.data?.installations ?? []).map(row => (
        <InstallationCard
          key={row.id}
          workspaceId={workspaceId}
          row={row}
          fields={fieldsByPackage.get(row.package_id) ?? []}
          oauth={packages.some(pkg => pkg.id === row.package_id && pkg.auth_type === 'oauth2')}
          sync={packages.some(
            pkg => pkg.id === row.package_id && pkg.capabilities.includes('sync'),
          )}
          onChanged={refresh}
        />
      ))}
    </SettingsSection>
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
      <Text as="h3" variant="body-sm" weight="semibold">{pkg.name}</Text>
      <Text as="p" variant="body-sm" tone="muted">
        {pkg.name} keeps its existing installations and grants.
      </Text>
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

function PackageCard({
  workspaceId,
  pkg,
  onChanged,
}: {
  workspaceId: string;
  pkg: { id: string; name: string; description: string; configuration: ConnectorPackageField[] };
  onChanged: () => void;
}) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const install = useMutation({
    mutationFn: () => connectorPackagesApi.install(workspaceId, pkg.id, values),
    onSuccess: onChanged,
  });
  return (
    <Surface border="subtle" radius="card" padding="md" className="mt-3 flex flex-col gap-3">
      <Text as="h3" variant="body-sm" weight="semibold">{pkg.name}</Text>
      <Text as="p" variant="body-sm" tone="muted">{pkg.description}</Text>
      <AppSettingsForm
        schema={configurationSchema(pkg.configuration, true)}
        value={values}
        onChange={setValues}
        idPrefix={pkg.id}
      />
      <Button type="button" size="sm" onClick={() => install.mutate()}>Add</Button>
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
}: {
  workspaceId: string;
  row: ConnectorPackageInstallation;
  fields: ConnectorPackageField[];
  sync: boolean;
  oauth: boolean;
  onChanged: () => void;
}) {
  const available = row.package_available !== false;
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, unknown>>({ ...row.config });
  const options = useQuery({
    queryKey: ['connectors', 'package-options', workspaceId, row.id, row.revision],
    enabled: open && fields.some(field => field.options_from),
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
    onSuccess: () => {
      setOpen(false);
      onChanged();
    },
  });
  return (
    <Surface border="subtle" radius="card" padding="md" className="mt-3 flex flex-col gap-2">
      <Text as="p" variant="body-sm">
        {row.name} · {row.enabled ? 'Enabled' : 'Disabled'} · {row.health_status}
        {available ? '' : ' · Package unavailable'}
      </Text>
      {available && open ? (
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
      {authorize.isError && <Text as="p" role="alert" variant="body-sm">{authorize.error.message}</Text>}
      <div className="flex gap-2">
        {available && oauth && (
          <Button type="button" size="sm" variant="secondary" onClick={() => authorize.mutate()}>
            Authorize
          </Button>
        )}
        {available ? (
          <Button type="button" size="sm" variant="secondary" onClick={() => setOpen(true)}>
            Configure
          </Button>
        ) : null}
        {available && row.upgrade_available ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => connectorPackagesApi.upgrade(workspaceId, row.id).then(onChanged)}
          >
            Upgrade
          </Button>
        ) : null}
        {row.rollback_available ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => connectorPackagesApi.rollback(workspaceId, row.id).then(onChanged)}
          >
            Roll back
          </Button>
        ) : null}
        {available || row.enabled ? (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => connectorPackagesApi.setEnabled(workspaceId, row.id, available ? !row.enabled : false).then(onChanged)}
          >
            {row.enabled ? 'Disable' : 'Enable'}
          </Button>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => connectorPackagesApi.remove(workspaceId, row.id).then(onChanged)}
        >
          Remove
        </Button>
      </div>
      {sync ? <ConnectorBindingsPanel connectorId={row.id} /> : null}
    </Surface>
  );
}
