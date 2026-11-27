/**
 * Connectors panel — home lists connected instances; Add Connector opens
 * the in-repo vetted catalog (native + MCP). Gmail/QuickBooks wizards live
 * on a connected row after install, not on home.
 *
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Cable,
  HeartPulse,
  List,
  MessageCircle,
  Pencil,
  PlugZap,
  Plus,
  RefreshCw,
  Settings,
  Trash2,
} from "lucide-react";
import { formatDistanceToNow, parseISO } from "date-fns";

import {
  connectorsApi,
  MCP_OAUTH_MESSAGE_TYPE,
  type CatalogEntry,
  type ConnectorResponse,
} from "../../../api/connectors";
import { getSessionGeneration } from "../../../api/session";
import { managedChannelsApi, type ManagedChannel } from "../../../api/managedChannels";
import { useAgentive } from "../../../context/AgentiveContext";
import {
  configurationSchema,
  ConnectorPackageDetail,
} from "../connectors/ConnectorPackagePanel";
import {
  connectorPackagesApi,
  type ConnectorPackageField,
} from "../../../api/connectorPackages";
import { AppSettingsForm } from "../../../components/apps/AppSettingsForm";
import { Button } from "../../../components/ui/Button";
import { connectorRemoval } from "../connectors/connectorRemoval";
import { HoverTooltip } from "../../../components/ui/HoverTooltip";
import { Modal } from "../../../components/ui/Modal";
import {
  KebabMenu,
  type KebabMenuItem,
} from "../../../components/ui/KebabMenu";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton, Surface, Text } from "../../../ui";
import { AsyncBoundary } from "../../../patterns";
import { useConfirm } from "../../../context/ConfirmContext";
import { useToast } from "../../../context/ToastContext";
import { useAuthOptional } from "../../../context/AuthContext";
import { useScopeOptional } from "../../../context/ScopeContext";
import { SettingsSection, StatusPill } from "../components/Field";
import { SettingsPageHeader } from "../components/SettingsPageHeader";
import { ConnectorEditModal } from "./ConnectorEditModal";
import { ConnectorBrandIcon } from "../connectors/ConnectorBrandIcon";
import { ConnectorCatalogPanel } from "../connectors/ConnectorCatalogPanel";
import type { ManagedChannelShareSource } from "../connectors/ManagedChannelShareControl";
import { ManagedChannelLoadNotice, ManagedChannelSettings, ManagedChannelStatus } from "../connectors/ManagedChannelSettings";
import { currentManagedChannels, managedProfileOptions } from "../connectors/managedChannelQueries";
import { SegmentedControl } from "../../../components/ui/SegmentedControl";
import {
  McpToolsInspector,
  toolCountLabel,
} from "../connectors/McpToolsInspector";
import { mcpConnectorDetails } from "../connectors/mcpConnectorDetails";

function selectPath(body: unknown, path: string): unknown {
  if (!path || path === "$") return body;
  if (!path.startsWith("$.")) return undefined;
  let current = body;
  for (const part of path.slice(2).split(".")) {
    if (Array.isArray(current)) {
      current = current.map((item) =>
        item && typeof item === "object"
          ? (item as Record<string, unknown>)[part]
          : undefined,
      );
    } else if (current && typeof current === "object") {
      current = (current as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return current;
}

function PackageIntervalSettings({
  connector,
  fields,
  onSaved,
}: {
  connector: ConnectorResponse;
  fields: ConnectorPackageField[];
  onSaved: () => void;
}) {
  const editable = fields.filter(
    (field) =>
      field.type === "number" ||
      field.type === "boolean" ||
      (field.type === "select" && (field.options || field.options_from)),
  );
  const saved = (key: string) => {
    const value = connector.auth_state?.[key];
    return Array.isArray(value) ? value.map(String) : value;
  };
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const initial: Record<string, unknown> = {};
    for (const field of editable) {
      if (field.key === "sync_interval_seconds") {
        initial[field.key] = connector.sync_interval_seconds;
      } else if (field.options_from) {
        initial[field.key] = saved(field.key) ?? (field.multiple ? [] : "");
      }
    }
    return initial;
  });
  const [choices, setChoices] = useState<
    Record<string, { value: string; label: string }[]>
  >({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const actionKey = editable
    .map((field) =>
      field.options_from ? `${field.key}:${field.options_from.action}` : "",
    )
    .filter(Boolean)
    .join(",");
  useEffect(() => {
    let cancelled = false;
    for (const field of editable) {
      const source = field.options_from;
      if (!source) continue;
      void connectorsApi.readAction(connector.id, source.action).then(
        (result) => {
          if (cancelled) return;
          const items = selectPath(result.body, source.items);
          const options = (Array.isArray(items) ? items : [])
            .map((item) => ({
              value: String(selectPath(item, source.value) ?? ""),
              label: String(selectPath(item, source.label) ?? ""),
            }))
            .filter((option) => option.value);
          setChoices((current) => ({ ...current, [field.key]: options }));
        },
        () => {
          if (!cancelled) setError("Could not load choices");
        },
      );
    }
    return () => {
      cancelled = true;
    };
  }, [connector.id, actionKey]);
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const raw = values.sync_interval_seconds;
      if (typeof raw === "number" && !Number.isNaN(raw)) {
        await connectorsApi.patch(connector.id, {
          sync_interval_seconds: Math.max(60, Math.trunc(raw)),
        });
      }
      const selected: Record<string, unknown> = {};
      for (const field of editable) {
        if (field.options_from) selected[field.key] = values[field.key];
      }
      if (Object.keys(selected).length > 0) {
        await connectorsApi.saveConfiguration(connector.id, selected);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Settings update failed");
    } finally {
      setSaving(false);
    }
  };
  if (editable.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-3">
      <AppSettingsForm
        schema={configurationSchema(editable, false, choices)}
        value={values}
        onChange={setValues}
        idPrefix={connector.id}
      />
      {error ? (
        <Text variant="body-sm" tone="subtle" as="p">
          {error}
        </Text>
      ) : null}
      <Button type="button" size="sm" onClick={() => void save()} disabled={saving}>
        {saving ? "Saving…" : "Save settings"}
      </Button>
    </div>
  );
}

function formatLastSynced(iso: string | null): string {
  if (!iso) return "never";
  try {
    return `${formatDistanceToNow(parseISO(iso))} ago`;
  } catch {
    return iso;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function McpConnectorSummary({
  connector,
  title,
}: {
  connector: ConnectorResponse;
  title?: string;
}) {
  const details = mcpConnectorDetails(connector);
  const displayTitle = title || details.title;
  const metaBits = [
    details.transportLabel,
    details.version ? `v${details.version}` : "",
    details.endpoint,
  ].filter(Boolean);
  const showRegistry =
    Boolean(details.registryName) && details.registryName !== displayTitle;

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Text
          variant="heading-sm"
          weight="semibold"
          as="span"
          className="min-w-0 break-words"
        >
          {displayTitle}
        </Text>
        <StatusPill
          state={
            connector.health_status === "ok"
              ? "ok"
              : connector.health_status === "error"
                ? "warn"
                : connector.health_status === "degraded"
                  ? "warn"
                  : "idle"
          }
        >
          {connector.health_status === "ok"
            ? "Connected"
            : connector.health_status === "error"
              ? "Connection error"
              : connector.health_status === "degraded"
                ? "Needs attention"
                : "Not checked"}
        </StatusPill>
      </div>
      {showRegistry ? (
        <Text variant="mono" as="p" className="mt-1">
          {details.registryName}
        </Text>
      ) : null}
      <Text variant="body-sm" tone="subtle" as="p" className="mt-1 break-words">
        {connector.connection_mode === "shared"
          ? "Workspace connection"
          : "Personal connection"}{" "}
        · MCP
      </Text>
      {metaBits.length > 0 ? (
        <Text variant="meta" tone="subtle" as="p" className="mt-1 break-all">
          {metaBits.join(" · ")}
        </Text>
      ) : null}
      {connector.last_error ? (
        <Text
          variant="body-sm"
          tone="danger"
          as="p"
          className="mt-2 break-words"
        >
          {connector.last_error}
        </Text>
      ) : null}
    </>
  );
}

function ConfigureCog({ label, onClick }: { label: string; onClick: () => void }) {
  return <IconButton size="md" label={label} title={label} onClick={onClick}><Settings size={16} aria-hidden /></IconButton>;
}

function RemoveConnector({ name, disabled, onClick }: {name: string; disabled?: boolean; onClick: () => void}) {
  const label = `Remove ${name}`;
  return <HoverTooltip label={label}><IconButton size="md" label={label} disabled={disabled} onClick={onClick}>
    <Trash2 size={15} aria-hidden className="text-[var(--danger-fg)]" />
  </IconButton></HoverTooltip>;
}

function InstallationSwitch({ name, checked, disabled, busy, onChange }: {
  name: string; checked: boolean; disabled: boolean; busy: boolean; onChange: () => void;
}) {
  const label = `${checked ? "Disable" : "Enable"} ${name}`;
  return <button type="button" role="switch" aria-checked={checked} aria-label={label}
    title={label} aria-busy={busy} disabled={disabled || busy} onClick={onChange}
    className="inline-flex h-9 w-10 shrink-0 items-center justify-center rounded-md focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)] disabled:opacity-50">
    <span aria-hidden className={`inline-flex h-5 w-9 items-center rounded-full ${checked ? "bg-[var(--brand-accent)]" : "bg-[var(--badge-muted-bg)]"}`}>
      <span className={`h-4 w-4 rounded-full bg-white shadow-[var(--shadow-sm)] transition-transform ${checked ? "translate-x-[18px]" : "translate-x-[2px]"}`} />
    </span>
  </button>;
}

function ConnectedConnectorRow({ children }: { children: ReactNode }) {
  return (
    <Surface as="li" tone="panel-2" border="subtle" radius="card" padding="sm"
      className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
      {children}
    </Surface>
  );
}

export function ConnectorsSection({ shareSource }: { shareSource?: ManagedChannelShareSource } = {}) {
  const auth = useAuthOptional();
  const scope = useScopeOptional();
  const principalId = auth?.user?.id ?? "";
  const workspaceId = scope?.scope?.workspaceId ?? "";
  // A reviewed host must bind its optional reader to the current local auth
  // generation. Never carry a prior actor/workspace/session source forward.
  const currentShareSource = shareSource && principalId && workspaceId && auth?.token && !auth.loading &&
    shareSource.scope.principalId === principalId && shareSource.scope.workspaceId === workspaceId &&
    shareSource.scope.sessionFence === String(getSessionGeneration()) ? shareSource : undefined;

  // A scope switch must change the mounted observer and discard open panels.
  // Removing an unscoped cache alone leaves its observer displaying old rows.
  return (
    <ScopedConnectorsSection
      key={JSON.stringify([principalId, workspaceId])}
      principalId={principalId}
      workspaceId={workspaceId}
      shareSource={currentShareSource}
    />
  );
}

function ScopedConnectorsSection({
  principalId,
  workspaceId,
  shareSource,
}: {
  principalId: string;
  workspaceId: string;
  shareSource?: ManagedChannelShareSource;
}) {
  const isCurrentPanel = useRef(true);
  useEffect(() => {
    isCurrentPanel.current = true;
    return () => {
      isCurrentPanel.current = false;
    };
  }, []);
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const auth = useAuthOptional();
  const myId = auth?.user?.id ?? "";
  const scope = useScopeOptional();
  const { enabled: whatsappEnabled } = useAgentive();
  const wsRole = scope?.activeWorkspace?.your_role;
  // Workspace admins manage shared rows they did not install (edit,
  // re-auth, sync, delete). Mirror config (Gmail labels, QuickBooks
  // settings) stays owner-only.
  const isWsAdmin = wsRole === "admin" || wsRole === "owner";
  // Custom MCP mount is admin-only on the server (arbitrary URL). Hide
  // the control from members/guests so they don't fill a form that 403s.
  const canReadManaged = Boolean(workspaceId && isWsAdmin && !scope?.isPersonal);

  const { hash, key: locationKey } = useLocation();
  const navigate = useNavigate();
  const showLibrary = hash === "#connector-library";
  const [configuringId, setConfiguringId] = useState<string | null>(null);
  const personalWorkspaceId = scope?.workspaces?.find(item => item.kind === 'personal' && item.your_role === 'owner')?.id
    ?? (scope?.isPersonal && isWsAdmin ? workspaceId : undefined);
  const packageTargets = { workspace: canReadManaged ? workspaceId : undefined, personal: personalWorkspaceId };
  const canReadPackages = Boolean(workspaceId && (canReadManaged || personalWorkspaceId));
  const libraryPackages = useQuery({
    queryKey: ["connectors", "packages", principalId, workspaceId],
    queryFn: () => connectorPackagesApi.list(workspaceId),
    enabled: canReadPackages || Boolean(configuringId),
  });
  const packageData = canReadPackages ? libraryPackages.data : undefined;
  const installedPackages = packageData?.installations ?? [];
  const connectedHostLines = (packageData?.packages ?? []).filter(
    pkg => pkg.host_managed && pkg.grant?.present,
  );
  const [packageSelection, setPackageSelection] = useState<{
    id: string; installationId?: string; locationKey: string;
  } | null>(null);
  const packageDetail = packageSelection?.locationKey === locationKey ? packageSelection : null;
  if (packageSelection && !packageDetail) setPackageSelection(null);
  const selectedPackage = packageData?.packages.find(pkg => pkg.id === packageDetail?.id);
  const rescan = useMutation({
    mutationFn: (_directoryVisit: string) => connectorPackagesApi.rescan(workspaceId),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["connectors"] }); },
  });
  const hostLineStatus = libraryPackages.isError
    ? "unavailable"
    : libraryPackages.fetchStatus === "fetching"
      ? "loading"
      : connectedHostLines.length > 0
        ? "existing"
        : "new";
  const [managedSelection, setManagedSelection] = useState<{
    slug: string; name: string; action: ManagedChannel["action"]; locationKey: string;
  } | null>(null);
  // Browser Back/Forward closes setup. Do not revive an old selection when
  // revisiting a history entry or let its late mutation pull focus back here.
  if (managedSelection && managedSelection.locationKey !== locationKey) {
    setManagedSelection(null);
  }
  function selectManaged(channel: ManagedChannel) {
    if (channel.connected && channel.definition.visitor_surface) void managed.refetch();
    setManagedSelection({
      slug: channel.definition.slug, name: channel.definition.display_name,
      action: channel.action, locationKey,
    });
  }
  const addConnector = useRef<HTMLButtonElement>(null);
  const wasLibrary = useRef(showLibrary);
  useEffect(() => {
    if (!showLibrary && wasLibrary.current) addConnector.current?.focus({ preventScroll: true });
    wasLibrary.current = showLibrary;
  }, [showLibrary]);
  const [editing, setEditing] = useState<ConnectorResponse | null>(null);
  // Held by id, not by object: a snapshot taken at click time went stale the
  // moment Refresh re-discovered the tools, so the inspector kept showing the
  // old tool list behind a fresh one.
  const [inspectingId, setInspectingId] = useState<string | null>(null);
  const [reauthPending, setReauthPending] = useState<string | null>(null);
  const [scopeFilter, setScopeFilter] = useState<"all" | "shared" | "per_user">(
    "all",
  );

  const list = useQuery({
    queryKey: ["connectors", "list", principalId, workspaceId],
    queryFn: () => connectorsApi.list(),
  });

  const managedQueryKey = ["connectors", "managed-channels", principalId, workspaceId] as const;
  const managed = useQuery({
    queryKey: managedQueryKey,
    queryFn: ({ signal }) => managedChannelsApi.list(workspaceId, signal),
    enabled: canReadManaged,
    refetchOnMount: "always",
    retry: false,
  });
  const showManaged = canReadManaged && scopeFilter !== "per_user";
  const connectedManaged = showManaged ? currentManagedChannels(managed).filter(
    row => row.connected && !connectedHostLines.some(pkg => pkg.id === row.definition.slug),
  ) : [];
  const currentScope = useRef(true);
  useEffect(() => {
    currentScope.current = true;
    return () => { currentScope.current = false; };
  }, []);
  type InstalledChannel = Extract<ManagedChannel, {connected: true}>;
  const channelToggle = useMutation({
    mutationFn: async (channel: InstalledChannel) => {
      const session = getSessionGeneration();
      const slug = channel.definition.slug;
      const generation = { provision_id: channel.provision_id, route_epoch: channel.route_epoch };
      const enable = channel.state === "inactive" || channel.enabled === false;
      if (enable && !channel.can_enable) throw new Error();
      if (enable && channel.configuration_kind === "published-public-profile-v1") {
        const profile = await qc.fetchQuery(managedProfileOptions(principalId, workspaceId, slug, generation, channel.definition.visitor_surface));
        if (!profile.eligible_profiles.some(choice => choice.key === profile.profile_key)) throw new Error();
      }
      if (!currentScope.current || session !== getSessionGeneration()) throw new Error();
      await managedChannelsApi.change(workspaceId, slug, {kind: "enabled", enabled: enable, generation});
      const fresh = await managed.refetch();
      const next = fresh.data?.find(item => item.definition.slug === slug);
      if (!fresh.isSuccess || !next?.connected || next.provision_id !== channel.provision_id ||
          (next.enabled ?? (next.state === "active")) !== enable) throw new Error();
      if (enable && next.state === "unavailable") toast.showToast("Enabled. Waiting for the host to be ready.", "info");
    },
    onError: () => {
      void managed.refetch();
      toast.showToast("Could not confirm the channel change. Check readiness in Configure.", "error");
    },
    onSettled: () => { void libraryPackages.refetch(); },
  });
  const packageToggle = useMutation({
    mutationFn: async (row: (typeof installedPackages)[number]) => {
      await connectorPackagesApi.setEnabled(workspaceId, row.id, !row.enabled);
      const fresh = await libraryPackages.refetch();
      if (!fresh.isSuccess || fresh.data.installations.find(item => item.id === row.id)?.enabled !== !row.enabled) throw new Error();
    },
    onError: () => { void libraryPackages.refetch(); toast.showToast("Could not confirm the installation change. Check configuration and health.", "error"); },
  });
  const standaloneToggle = useMutation({
    mutationFn: async (row: ConnectorResponse) => {
      const enabled = row.standalone_mcp_paused === true;
      await connectorsApi.setStandaloneMcpEnabled(row.id, enabled);
      const fresh = await list.refetch();
      if (!fresh.isSuccess || fresh.data.connectors.find(item => item.id === row.id)?.standalone_mcp_paused !== !enabled) throw new Error();
    },
    onError: () => { void list.refetch(); toast.showToast("Could not confirm the MCP change. Check the connection and try again.", "error"); },
  });
  const legacySyncToggle = useMutation({
    mutationFn: async (row: ConnectorResponse) => {
      const enabled = !row.legacy_sync_enabled;
      await connectorsApi.setLegacySyncEnabled(row.id, enabled);
      const fresh = await list.refetch();
      if (!fresh.isSuccess || fresh.data.connectors.find(item => item.id === row.id)?.legacy_sync_enabled !== enabled) throw new Error();
    },
    onError: () => { void list.refetch(); toast.showToast("Could not confirm the sync change. Check the connector configuration and try again.", "error"); },
  });
  const removeInstalled = useMutation({
    mutationFn: async (target: { id: string; name: string; channel?: InstalledChannel; workspaceGrant?: boolean }) => {
      const session = getSessionGeneration();
      const approved = await confirm(connectorRemoval(target.name, target.workspaceGrant));
      if (!approved || !currentScope.current || session !== getSessionGeneration()) return;
      if (target.channel) {
        await managedChannelsApi.change(workspaceId, target.channel.definition.slug, {
          kind: "remove", generation: { provision_id: target.id, route_epoch: target.channel.route_epoch },
        });
      } else {
        await connectorPackagesApi.remove(workspaceId, target.id);
      }
      await qc.invalidateQueries({queryKey: ["connectors"]});
    },
    onError: () => toast.showToast("Could not confirm removal. Check the current installation before trying again.", "error"),
  });
  function channelRemove(channel: ManagedChannel | undefined, workspaceGrant = false) {
    return channel?.connected && <RemoveConnector name={channel.definition.display_name} disabled={removeInstalled.isPending || channelToggle.isPending}
      onClick={() => removeInstalled.mutate({id: channel.provision_id, name: channel.definition.display_name, channel, workspaceGrant})} />;
  }
  function channelSwitch(channel: ManagedChannel | undefined) {
    if (!channel?.connected) return null;
    const enabled = channel.enabled ?? (channel.state === "unavailable" ? undefined : channel.state === "active");
    if (enabled === undefined) return null;
    return <InstallationSwitch name={channel.definition.display_name} checked={enabled} disabled={!enabled && !channel.can_enable || channelToggle.isPending}
      busy={channelToggle.isPending && channelToggle.variables.provision_id === channel.provision_id} onChange={() => channelToggle.mutate(channel)} />;
  }

  const selectedManaged = currentManagedChannels(managed).find(
    row => selectedPackage?.id === row.definition.slug && row.connected,
  );
  const currentConfiguredShare = shareSource && managedSelection?.action === "configure" &&
    currentManagedChannels(managed).some(row => row.connected && row.state === "active" &&
      row.definition.slug === managedSelection.slug) ? shareSource : undefined;


  const inspecting =
    (list.data?.connectors ?? []).find((c) => c.id === inspectingId) ?? null;
  const inspectingIsNative = !!inspecting && inspecting.subclass_slug !== "mcp";
  const inspectedTools = useQuery({
    queryKey: ["connectors", "tools", principalId, workspaceId, inspectingId],
    queryFn: () => connectorsApi.listTools(inspectingId as string),
    enabled: !!inspectingId && inspectingIsNative,
    staleTime: 30_000,
  });

  const catalog = useQuery({
    queryKey: ["connectors", "catalog", principalId, workspaceId],
    queryFn: () => connectorsApi.listCatalog(),
  });

  const syncMut = useMutation({
    mutationFn: (id: string) => connectorsApi.sync(id),
    onSuccess: (stats) => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      qc.invalidateQueries({ queryKey: ["conflicts"] });
      toast.showToast(
        `Sync done — ${stats.created} created, ${stats.updated} updated, ${stats.conflict} conflicts`,
        "success",
      );
    },
    onError: (err: Error) => {
      toast.showToast(err.message || "Sync failed", "error");
    },
  });

  const refreshMcpMut = useMutation({
    mutationFn: (id: string) => connectorsApi.refreshMcp(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      toast.showToast("MCP tools refreshed", "success");
    },
    onError: (err: Error) => {
      toast.showToast(err.message || "MCP refresh failed", "error");
    },
  });

  const healthMut = useMutation({
    mutationFn: (id: string) => connectorsApi.health(id),
    onSuccess: (h) => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      toast.showToast(
        h.status === "ok"
          ? `Healthy — ${h.tool_count} tools`
          : h.status === "degraded"
            ? `Degraded — ${h.last_error || "reachable but not fully usable"}`
            : `Unhealthy — ${h.last_error || h.status}`,
        h.status === "ok" ? "success" : "error",
      );
    },
    onError: (err: Error) => {
      toast.showToast(err.message || "Health check failed", "error");
    },
  });

  const reauthMut = useMutation({
    mutationFn: (id: string) => connectorsApi.reauthorizeMcp(id),
    onSuccess: (res) => {
      // An authorization response arriving after a switch belongs to the old
      // panel. Do not open its consent popup in the user's new workspace.
      if (!isCurrentPanel.current) return;
      const popup = window.open(
        res.consent_url,
        `mcp-reauth-${res.connector_id}`,
        "width=600,height=720,scrollbars=yes",
      );
      if (!popup) {
        toast.showToast(
          "Popup blocked. Allow popups to reconnect this connector.",
          "error",
        );
        return;
      }
      setReauthPending(res.connector_id);
      toast.showToast("Finish authorizing in the popup", "success");
    },
    onError: (err: Error) => {
      toast.showToast(err.message || "Reconnect failed", "error");
    },
  });

  // The OAuth callback page posts back to the opener. Without this the popup
  // completes, the connector is healthy again, and the list still shows the
  // stale error until the user reloads.
  useEffect(() => {
    if (!reauthPending) return;
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as {
        type?: string;
        ok?: boolean;
        error?: string;
      };
      if (!data || data.type !== MCP_OAUTH_MESSAGE_TYPE) return;
      setReauthPending(null);
      if (data.ok) {
        qc.invalidateQueries({ queryKey: ["connectors"] });
        toast.showToast("Connector reconnected", "success");
        return;
      }
      toast.showToast(data.error || "Authorization was cancelled", "error");
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [reauthPending, qc, toast]);

  const delMut = useMutation({
    mutationFn: (id: string) => connectorsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      toast.showToast("Connector deleted", "success");
    },
    onError: (err: Error) => {
      toast.showToast(err.message || "Failed to delete connector", "error");
    },
  });

  const handleDelete = async (c: ConnectorResponse) => {
    const session = getSessionGeneration();
    const ok = await confirm(connectorRemoval(instanceMeta(c).title));
    if (!ok || !currentScope.current || session !== getSessionGeneration()) return;
    delMut.mutate(c.id);
  };

  const connectors = list.isSuccess ? list.data.connectors : [];
  const visiblePackages = installedPackages.filter(row =>
    scopeFilter === "all" || (row.connection_mode ?? "shared") === scopeFilter
  );
  const visibleConnectors = connectors.filter(connector =>
    (scopeFilter === "all" || (connector.connection_mode || "per_user") === scopeFilter) &&
    !installedPackages.some(row => row.id === connector.id)
  );
  const catalogBySlug = new Map(
    (catalog.data?.entries ?? []).map((e: CatalogEntry) => [e.slug, e]),
  );
  const packageFields = new Map(
    (libraryPackages.data?.packages ?? []).map((pkg) => [
      pkg.id,
      pkg.configuration,
    ]),
  );
  const refetchConnectors = () => {
    qc.invalidateQueries({ queryKey: ["connectors"] });
  };

  const instanceMeta = (c: ConnectorResponse) => {
    const auth = asRecord(c.auth_state) ?? {};
    const catalogSlug =
      (typeof auth.catalog_slug === "string" && auth.catalog_slug) ||
      c.subclass_slug ||
      "";
    const pkg = catalogSlug ? catalogBySlug.get(catalogSlug) : undefined;
    const title = (c.label || "").trim() || undefined;
    const isShared = (c.connection_mode || "per_user") === "shared";
    if (c.subclass_slug === "mcp") {
      const mcp = mcpConnectorDetails(c);
      return {
        title: title || pkg?.display_name || mcp.title,
        icon: pkg?.icon || catalogSlug || "mcp",
        slug: catalogSlug,
        isMcp: true,
        isShared,
      };
    }
    return {
      title: title || pkg?.display_name || c.subclass_slug || c.kind,
      icon: pkg?.icon || catalogSlug || c.kind,
      slug: catalogSlug,
      isMcp: false,
      isShared,
    };
  };

  const configuring = connectors.find(row => row.id === configuringId);

  return (
    <div className="flex flex-col gap-5">
      {showLibrary ? (
        <>
          <SettingsPageHeader
            title="Connector library"
            description="Find API integrations, MCP servers, and messaging channels."
            actions={
              <Button
                type="button"
                variant="ghost"
                size="sm"
                icon={<ArrowLeft size={14} />}
                onClick={() => navigate("/settings#connectors")}
                aria-label="Back to connectors"
              >
                Back
              </Button>
            }
          />
          <Surface border="subtle" radius="card" padding="lg">
            <ConnectorCatalogPanel
              catalog={catalog}
              whatsappEnabled={whatsappEnabled}
              whatsappStatus={hostLineStatus}
              packages={packageData}
              permittedBindings={packageTargets}
              onOpenManifest={id => setPackageSelection({ id, locationKey })}
              directoryAdmin={canReadManaged ? {
                rescan: () => rescan.mutate(locationKey), pending: rescan.isPending,
                failed: rescan.variables === locationKey && rescan.isError,
                rejected: rescan.variables === locationKey ? rescan.data?.rejected ?? [] : [],
                dismiss: rescan.reset,
              } : undefined}
              managed={canReadManaged ? managed : undefined}
              onManageChannel={canReadManaged ? selectManaged : undefined}
              onInstalled={() => {
                refetchConnectors();
                navigate("/settings#connectors");
              }}
            />
          </Surface>
        </>
      ) : (
        <>
          <SettingsPageHeader
            title="Connectors"
            description="Configure integrations for Integral and manage connected tools."
            actions={
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  icon={<Plus size={14} />}
                  ref={addConnector}
                  onClick={() => navigate("/settings#connector-library")}
                  aria-label="Add Connector"
                >
                  Add Connector
                </Button>
            }
          />
          <SettingsSection
            title="Connected"
            description="Manage your connections and check that their tools are available."
          >
            <div className="mb-2 flex items-center gap-2">
              <SegmentedControl
                size="sm"
                ariaLabel="Filter by connection scope"
                value={scopeFilter}
                onChange={setScopeFilter}
                options={[
                  { value: "all", label: "All" },
                  { value: "shared", label: "Workspace" },
                  { value: "per_user", label: "Per-user" },
                ]}
              />
            </div>
            {whatsappEnabled && hostLineStatus === "unavailable" && scopeFilter !== "per_user" && (
              <Text as="p" variant="body-sm" tone="muted">Could not check host-managed lines.</Text>
            )}
            <AsyncBoundary query={list} emptyFallback={null} errorFallback={(err) => (
              <Text variant="body" tone="danger" as="p">Failed to load connectors: {err.message}</Text>
            )}>
              {() => null}
            </AsyncBoundary>
            {showManaged && <ManagedChannelLoadNotice query={managed} />}
            {(whatsappEnabled && scopeFilter !== "per_user" && connectedHostLines.length > 0) || visibleConnectors.length > 0 || connectedManaged.length > 0 || visiblePackages.length > 0 ? (
              <ul className="flex flex-col gap-2">
                {whatsappEnabled && scopeFilter !== "per_user" && connectedHostLines.map((pkg) => (
                  <ConnectedConnectorRow key={`host:${pkg.id}`}>
                    <div className="flex min-w-0 items-start gap-3">
                      <MessageCircle size={24} className="m-1 shrink-0" aria-hidden />
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <Text variant="heading-sm" weight="semibold" as="h3">{pkg.name}</Text>
                        <StatusPill state={pkg.grant?.enabled ? "ok" : "idle"}>
                          {pkg.grant?.enabled ? "Enabled" : "Disabled"}
                        </StatusPill>
                        <StatusPill state="idle">Workspace</StatusPill>
                        <Text as="p" variant="body-sm" tone="subtle" className="basis-full">{pkg.description}</Text>
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <ConfigureCog label={`Configure ${pkg.name}`} onClick={() => setPackageSelection({ id: pkg.id, locationKey })} />
                      {channelSwitch(currentManagedChannels(managed).find(channel => channel.connected && channel.definition.slug === pkg.id))}
                      {channelRemove(currentManagedChannels(managed).find(channel => channel.connected && channel.definition.slug === pkg.id), true)}
                    </div>
                  </ConnectedConnectorRow>
                ))}
                {visiblePackages.map(row => {
                  const siblings = installedPackages.filter(item => item.package_id === row.package_id && item.name === row.name);
                  const displayName = siblings.length > 1 ? `${row.name} · ${row.id}` : row.name;
                  return (
                  <ConnectedConnectorRow key={row.id}>
                    <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <ConnectorBrandIcon icon={catalogBySlug.get(row.package_id)?.icon ?? row.package_id} label={row.name} />
                      <Text as="h3" variant="heading-sm" weight="semibold">{displayName}</Text>
                      <StatusPill state={row.enabled ? "ok" : "idle"}>{row.enabled ? "Enabled" : "Disabled"}</StatusPill>
                      <StatusPill state="idle">{row.connection_mode === "per_user" ? "Personal" : "Workspace"}</StatusPill>
                      {row.package_available === false && <StatusPill state="warn">Package unavailable</StatusPill>}
                    </div>
                    <Text as="p" variant="body-sm" tone="subtle">{packageData?.packages.find(pkg => pkg.id === row.package_id)?.description}</Text></div>
                    <div className="ml-auto flex items-center gap-1">
                      <ConfigureCog label={`${row.package_available === false ? "Manage" : "Configure"} ${displayName}`}
                        onClick={() => setPackageSelection({ id: row.package_id, installationId: row.id, locationKey })} />
                      <InstallationSwitch name={displayName} checked={row.enabled}
                        disabled={libraryPackages.fetchStatus !== "idle" || libraryPackages.isError || packageToggle.isPending || (!row.enabled && row.package_available === false)}
                        busy={packageToggle.isPending && packageToggle.variables.id === row.id} onChange={() => packageToggle.mutate(row)} />
                      <RemoveConnector name={displayName} disabled={removeInstalled.isPending || packageToggle.isPending || libraryPackages.isFetching} onClick={() => removeInstalled.mutate({id: row.id, name: displayName})} />
                    </div>
                  </ConnectedConnectorRow>
                  );
                })}
                {connectedManaged.map(channel => (
                  <ConnectedConnectorRow key={`managed:${channel.definition.slug}`}>
                    <div className="flex min-w-0 items-start gap-3">
                      <MessageCircle size={24} className="m-1 shrink-0" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Text as="h3" variant="heading-sm" weight="semibold">{channel.definition.display_name}</Text>
                          <ManagedChannelStatus channel={channel} />
                          <StatusPill state="idle">Workspace</StatusPill>
                        </div>
                        <Text as="p" variant="body-sm" tone="subtle">{channel.definition.description}</Text>
                      </div>
                    </div>
                    <div className="ml-auto flex items-center gap-1">
                      <ConfigureCog label={`Configure ${channel.definition.display_name}`} onClick={() => selectManaged(channel)} />
                      {channelSwitch(channel)}
                      {channelRemove(channel)}
                    </div>
                  </ConnectedConnectorRow>
                ))}
                {visibleConnectors.map((c) => {
                  const meta = instanceMeta(c);
                  const isMine =
                    !myId ||
                    c.owner === myId ||
                    c.owner === auth?.user?.user_id;
                  const canAdminister =
                    isMine || (meta.isShared && isWsAdmin);
                  const isSharedView = meta.isShared && !canAdminister;
                  return (
                    <ConnectedConnectorRow key={c.id}>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start gap-3">
                          <ConnectorBrandIcon
                            icon={meta.icon}
                            label={meta.title}
                          />
                          <div className="min-w-0 flex-1">
                            {meta.isMcp ? (
                              <McpConnectorSummary
                                connector={c}
                                title={meta.title}
                              />
                            ) : (
                              <>
                                <div className="flex flex-wrap items-center gap-2">
                                  <Text
                                    variant="heading-sm"
                                    weight="semibold"
                                    as="span"
                                  >
                                    {meta.title}
                                  </Text>
                                  <StatusPill
                                    state={
                                      c.conflict_policy ? "ok" : "idle"
                                    }
                                  >
                                    {c.conflict_policy ||
                                      "no conflict rule"}
                                  </StatusPill>
                                  {meta.isShared ? (
                                    <StatusPill state="idle">
                                      Workspace
                                    </StatusPill>
                                  ) : null}
                                </div>
                                {isSharedView ? (
                                  <Text
                                    variant="body-sm"
                                    tone="subtle"
                                    as="p"
                                    className="mt-1"
                                  >
                                    Workspace connection — actions run as this
                                    account. Add your own from the library
                                    for a personal connection.
                                  </Text>
                                ) : null}
                                <Text
                                  variant="body-sm"
                                  tone="subtle"
                                  as="p"
                                  className="mt-1"
                                >
                                  {`Sync every ${c.sync_interval_seconds}s · last synced ${formatLastSynced(c.last_synced_at)}`}
                                </Text>
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="ml-auto flex items-center gap-1">
                      <KebabMenu icon={<Settings size={16} />} portal
                        ariaLabel={`${canAdminister && (c.mapping_profile !== "package" || meta.isMcp || c.settings_surface) ? "Manage" : "Actions for"} ${meta.title}`}
                        menuTitle={meta.title}
                        items={[
                          { key: "tools", label: meta.isMcp ? `View ${toolCountLabel(mcpConnectorDetails(c).tools.length)}` : "View tools",
                            icon: <List size={14} />, onClick: () => setInspectingId(c.id) },
                          ...(meta.isMcp ? [
                            {key: "health", label: "Check connection", icon: <HeartPulse size={14} />, onClick: () => healthMut.mutate(c.id), disabled: healthMut.isPending},
                          ] : canAdminister && c.legacy_sync_enabled !== false && (c.mapping_profile !== "package" || c.enabled) ? [
                            {key: "sync", label: "Sync now", icon: <RefreshCw size={14} />, onClick: () => syncMut.mutate(c.id), disabled: syncMut.isPending},
                          ] : []),
                          ...(canAdminister && meta.isMcp ? [
                            {key: "refresh", label: "Refresh tools", icon: <RefreshCw size={14} />, onClick: () => refreshMcpMut.mutate(c.id), disabled: refreshMcpMut.isPending},
                            {key: "reconnect", label: "Reconnect", icon: <PlugZap size={14} />, onClick: () => reauthMut.mutate(c.id), disabled: reauthMut.isPending},
                          ] : []),
                          ...(isMine && c.settings_surface ? [
                            {key: "configure", label: "Service settings", icon: <Settings size={14} />, onClick: () => setConfiguringId(c.id)},
                          ] : []),
                          ...(canAdminister && c.mapping_profile !== "package" ? [
                            {key: "edit", label: "Configure connection", icon: <Pencil size={14} />, onClick: () => setEditing(c)},
                          ] : []),
                        ] satisfies KebabMenuItem[]} />
                      {typeof c.legacy_sync_enabled === "boolean" && (
                        <InstallationSwitch name={meta.title} checked={c.legacy_sync_enabled}
                          disabled={!canAdminister || legacySyncToggle.isPending || list.isFetching}
                          busy={legacySyncToggle.isPending && legacySyncToggle.variables.id === c.id}
                          onChange={() => legacySyncToggle.mutate(c)} />
                      )}
                      {meta.isMcp && c.workspace_id === workspaceId && c.subclass_slug === "mcp" && c.mapping_profile !== "package" && (
                        <InstallationSwitch name={meta.title} checked={!c.standalone_mcp_paused}
                          disabled={!canAdminister || standaloneToggle.isPending || list.isFetching}
                          busy={standaloneToggle.isPending && standaloneToggle.variables.id === c.id}
                          onChange={() => standaloneToggle.mutate(c)} />
                      )}
                      {canAdminister && c.mapping_profile !== "package" && <RemoveConnector name={meta.title} disabled={delMut.isPending} onClick={() => { void handleDelete(c); }} />}
                      </div>
                    </ConnectedConnectorRow>
                  );
                })}
              </ul>
            ) : list.isSuccess && (!showManaged || (managed.isSuccess && managed.fetchStatus === "idle")) &&
                (!whatsappEnabled || libraryPackages.fetchStatus !== "fetching" || scopeFilter === "per_user") ? (
              connectors.length > 0 ? (
                <Text variant="body-sm" tone="subtle" as="p">No connectors match this filter.</Text>
              ) : (
                <EmptyState icon={<Cable size={24} className="text-[var(--text-subtle)]" />}
                  title="No connectors yet." description="Add an integration from the Connector Library." />
              )
            ) : null}
          </SettingsSection>
        </>
      )}

      {canReadManaged && managedSelection && (
        <ManagedChannelSettings
          selection={managedSelection}
          shareSource={currentConfiguredShare}
          canReadPublishedShare={Boolean(principalId && principalId === myId && auth?.token && !auth.loading)}
          principalId={principalId}
          workspaceId={workspaceId}
          query={managed}
          onAbandoned={() => { void qc.invalidateQueries({ queryKey: managedQueryKey, exact: true }); }}
          onClose={() => setManagedSelection(null)}
          onInstalled={() => {
            setManagedSelection(null);
            navigate("/settings#connectors");
          }}
        />
      )}
      <ConnectorEditModal
        open={editing !== null}
        onClose={() => setEditing(null)}
        connector={editing}
      />
      {inspecting ? (
        <McpToolsInspector
          connector={inspecting}
          title={instanceMeta(inspecting).title}
          onClose={() => setInspectingId(null)}
          tools={
            inspectingIsNative
              ? (inspectedTools.data?.tools.map((t) => ({
                  name: t.key,
                  description: t.description,
                  inputSchema: t.input_schema,
                  write: t.write,
                })) ?? null)
              : undefined
          }
          metaLine={
            inspectingIsNative
              ? `native · ${inspectedTools.data?.tools.length ?? 0} tools`
              : undefined
          }
          pending={inspectingIsNative && inspectedTools.isPending}
        />
      ) : null}
      {configuring && (
        <Modal open title={`Configure ${instanceMeta(configuring).title}`} onClose={() => setConfiguringId(null)}>
          <Modal.Body>
            {libraryPackages.isError
              ? <Text as="p" tone="danger" role="alert">Could not load connector settings.</Text>
              : libraryPackages.data
                ? <PackageIntervalSettings key={configuring.id} connector={configuring}
                    fields={packageFields.get(instanceMeta(configuring).slug) ?? []} onSaved={refetchConnectors} />
                : <Text as="p" tone="muted" role="status">Loading connector settings…</Text>}
          </Modal.Body>
          <Modal.Footer><Button type="button" variant="ghost" onClick={() => setConfiguringId(null)}>Close</Button></Modal.Footer>
        </Modal>
      )}
      {canReadPackages && packageDetail && (
        <ConnectorPackageDetail key={`${packageDetail.id}:${packageDetail.installationId ?? ""}`}
          workspaceId={workspaceId} pkg={selectedPackage}
          installations={installedPackages.filter(row => row.package_id === packageDetail.id)}
          initialInstallationId={packageDetail.installationId}
          targets={packageTargets}
          onClose={() => setPackageSelection(null)}
          onChanged={refetchConnectors}
          onInstalledInScope={(target, installation) => {
            refetchConnectors();
            if (target !== workspaceId) {
              scope?.setScope({ workspaceId: target });
              setPackageSelection(null);
              navigate("/settings#connectors");
            } else {
              setPackageSelection({ id: installation.package_id, installationId: installation.id, locationKey });
            }
          }}
          onManageChannel={selectedManaged ? () => {
            setPackageSelection(null);
            selectManaged(selectedManaged);
          } : undefined}
        />
      )}
    </div>
  );
}
