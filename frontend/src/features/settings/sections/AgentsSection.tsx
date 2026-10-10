/** Built-in Integral AI resident and authorized external MCP clients. */
import { useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { ChevronDown, Copy, Plug } from 'lucide-react';

import {
  connectedAgentsApi,
  type ConnectedAgent,
} from '../../../api/connectedAgents';
import { Badge } from '../../../components/ui/Badge';
import { Button } from '../../../components/ui/Button';
import { EmptyState } from '../../../components/ui/EmptyState';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '../../../components/ui/collapsible';
import { Inline, Stack, Surface, Text } from '../../../ui';
import { AsyncBoundary } from '../../../patterns';
import { getApiBaseURL } from '../../../config';
import { useConfirm } from '../../../context/ConfirmContext';
import { useToast } from '../../../context/ToastContext';
import { SettingsSection, StatusPill } from '../components/Field';
import type { HarnessProviderId } from '../types';

const CONNECTED_AGENTS_QUERY_KEY = ['connected-agents'] as const;

/** Absolute, copyable MCP endpoint URL. `getApiBaseURL()` yields an
 *  absolute `<origin>/api` in prod (VITE_API_URL set) or a relative `/api`
 *  in same-origin dev — prefix the dev case with the current origin so the
 *  string a user pastes into their MCP client is always fully qualified. */
function resolveMcpUrl(): string {
  const base = getApiBaseURL();
  return base.startsWith('http')
    ? `${base}/mcp`
    : `${window.location.origin}${base}/mcp`;
}

function formatGrantedAt(iso: string): string {
  if (!iso) return 'unknown';
  try {
    return `${formatDistanceToNow(parseISO(iso))} ago`;
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Resident harness rows
// ---------------------------------------------------------------------------

interface HarnessAgentRow {
  id: string;
  displayName: string;
  /** User-facing subtitle under the display name. */
  subtitle: string;
}


// ---------------------------------------------------------------------------
// Provider definitions
// ---------------------------------------------------------------------------

type ProviderKey = 'pydantic_native';

interface ProviderDef {
  key: ProviderKey;
  /** User-facing provider title (ops-layer pick). */
  label: string;
  /** Short blurb for the radio card. */
  blurb: string;
  /** Routing id stored in ``providers.defaultProviderId`` when this
   *  provider is the active harness. Shown only under Advanced. */
  routingId: HarnessProviderId;
  /** Provider registry id persisted on chat threads. */
  providerId: string;
  /** The synthetic row rendered under this provider. */
  agent: HarnessAgentRow;
}

const PROVIDERS: ProviderDef[] = [
  {
    key: 'pydantic_native',
    label: 'Integral AI',
    blurb: 'Integral Core’s Pydantic AI harness with scoped sessions and brokered tools.',
    routingId: 'pydantic-ai-native',
    providerId: 'integral_native',
    agent: {
      id: 'integral-native',
      displayName: 'Integral AI',
      subtitle: 'Integral Core resident Pydantic AI harness',
    },
  },
];

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function AgentRow({ agent }: { agent: HarnessAgentRow }) {
  return (
    <li
      className="
        flex flex-col gap-2.5 rounded-[var(--radius-input)]
        border border-[var(--border-subtle)] bg-[var(--panel)] px-3 py-2.5
      "
    >
      <div className="flex flex-wrap items-center gap-2 min-w-0">
        <span className="text-sm truncate max-w-[280px] font-medium text-[var(--text)]">
          {agent.displayName}
        </span>
        <span className="ml-auto text-xs text-[var(--text-subtle)]">
          always available
        </span>
      </div>
      <p className="text-xs text-[var(--text-muted)]">{agent.subtitle}</p>
    </li>
  );
}

function ProviderGroup({ provider }: { provider: ProviderDef }) {
  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--brand-accent-line)] bg-[var(--panel-2)] overflow-hidden">
      <header className="flex items-center gap-3 px-4 py-3 border-b border-[var(--border-subtle)]">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-[var(--text)]">{provider.label}</h3>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">{provider.blurb}</p>
        </div>
        <StatusPill state="ok">Always active</StatusPill>
      </header>
      <div className="px-4 py-3">
        <ul className="flex flex-col gap-2"><AgentRow agent={provider.agent} /></ul>
      </div>
    </section>
  );
}

function AdvancedHarnessIds() {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Surface
        tone="panel-2"
        border="subtle"
        radius="card"
        className="transition hover:border-[var(--panel-border)]"
      >
        <CollapsibleTrigger className="group flex w-full items-center justify-between gap-2 px-4 py-3 text-left">
          <Text as="span" variant="body" weight="medium">
            Advanced · technical IDs
          </Text>
          <Text
            as="span"
            tone="muted"
            className="flex transition-transform group-data-[state=open]:rotate-180"
          >
            <ChevronDown size={16} />
          </Text>
        </CollapsibleTrigger>
      </Surface>
      <CollapsibleContent className="pt-2">
        <Surface
          tone="panel"
          border="subtle"
          radius="card"
          padding="md"
          className="flex flex-col gap-2"
        >
          <Text variant="body-sm" tone="muted" as="p">
            Provider IDs are recorded on conversations. Settings routes are
            local assistant-selection keys. Prefer the harness picker above
            unless you are debugging providers.
          </Text>
          <dl className="flex flex-col gap-3 text-sm">
            {PROVIDERS.map(p => (
              <div key={p.key} className="flex flex-col gap-1">
                <Text variant="body-sm" tone="subtle" as="dt">
                  {p.label}
                </Text>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <Text variant="body-sm" as="dd" className="font-mono">
                    provider_id: {p.providerId}
                  </Text>
                  <Text variant="body-sm" as="dd" className="font-mono">
                    settings_route: {p.routingId}
                  </Text>
                </div>
              </div>
            ))}

          </dl>
        </Surface>
      </CollapsibleContent>
    </Collapsible>
  );
}

// ---------------------------------------------------------------------------
// Connected agents (MCP) — endpoint URL + connect instructions + grants
// ---------------------------------------------------------------------------

function ConnectedAgentsBlock() {
  const qc = useQueryClient();
  const { showToast } = useToast();
  const confirm = useConfirm();

  const mcpUrl = resolveMcpUrl();

  const list = useQuery({
    queryKey: CONNECTED_AGENTS_QUERY_KEY,
    queryFn: () => connectedAgentsApi.list(),
  });

  const revokeMut = useMutation({
    mutationFn: (clientId: string) => connectedAgentsApi.revoke(clientId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: CONNECTED_AGENTS_QUERY_KEY });
      showToast('Access revoked', 'success');
    },
    onError: (err: Error) => {
      showToast(err.message || 'Failed to revoke access', 'error');
    },
  });

  const copyMcpUrl = async () => {
    try {
      await navigator.clipboard.writeText(mcpUrl);
      showToast('MCP endpoint URL copied', 'success');
    } catch {
      showToast('Copy failed — select and copy manually', 'error');
    }
  };

  const handleRevoke = async (agent: ConnectedAgent) => {
    const ok = await confirm({
      title: `Revoke access for ${agent.client_name}?`,
      message:
        'This client will lose access immediately and must reconnect to ' +
        'regain it.',
      confirmLabel: 'Revoke',
      variant: 'danger',
    });
    if (!ok) return;
    revokeMut.mutate(agent.client_id);
  };

  return (
    <SettingsSection
      title="Connected agents"
      description="Connect an assistant such as Claude Desktop or Cursor using the address below. You can review and remove its access here."
    >
      <Stack gap="md">
        <Stack gap="sm">
          <Text variant="body-sm" weight="semibold" as="p">
            MCP endpoint
          </Text>
          <Inline gap="sm" align="center">
            <Text variant="mono" className="break-all">
              {mcpUrl}
            </Text>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              icon={<Copy size={12} />}
              onClick={copyMcpUrl}
              aria-label="Copy MCP endpoint URL"
            >
              Copy
            </Button>
          </Inline>
          <Text variant="body-sm" tone="muted" as="p">
            Add this URL to your MCP client (e.g. Claude Desktop). Your client
            will open a browser to sign in to Integral and approve access. No
            setup needed here.
          </Text>
        </Stack>

        <AsyncBoundary
          query={list}
          emptyFallback={
            <EmptyState
              icon={<Plug size={24} className="text-[var(--text-subtle)]" />}
              title="No connected agents yet"
              description="Assistants appear here after you connect them and approve access."
            />
          }
          errorFallback={(err) => (
            <Text variant="body" tone="danger" as="p">
              Failed to load connected agents: {err.message}
            </Text>
          )}
        >
          {(agents) => (
            <ul className="flex flex-col gap-2">
              {agents.map((agent) => (
                <Surface
                  key={agent.client_id}
                  as="li"
                  tone="panel-2"
                  border="subtle"
                  radius="card"
                  padding="md"
                  className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3"
                >
                  <Stack gap="xs" className="flex-1 min-w-0">
                    <Text variant="body-sm" weight="medium" as="p">
                      {agent.client_name}
                    </Text>
                    <Inline gap="xs" align="center" className="flex-wrap">
                      {agent.scopes.length === 0 ? (
                        <Text variant="body-sm" tone="subtle" as="span">
                          no scopes
                        </Text>
                      ) : (
                        agent.scopes.map((scope) => (
                          <Badge key={scope} variant="default">
                            {scope}
                          </Badge>
                        ))
                      )}
                    </Inline>
                    <Text variant="body-sm" tone="subtle" as="p">
                      Granted {formatGrantedAt(agent.granted_at)}
                    </Text>
                  </Stack>
                  <div className="shrink-0">
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      onClick={() => handleRevoke(agent)}
                      disabled={revokeMut.isPending}
                      aria-label={`Revoke access for ${agent.client_name}`}
                    >
                      Revoke
                    </Button>
                  </div>
                </Surface>
              ))}
            </ul>
          )}
        </AsyncBoundary>
      </Stack>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Section root
// ---------------------------------------------------------------------------

interface AgentsSectionProps {
  navigateToSection?: (id: string) => void;
}

export function AgentsSection(_props: AgentsSectionProps = {}) {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Agent
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Integral AI is your built-in resident assistant. Configure its model
          in AI Models to start working with it.
        </Text>
      </div>

      <SettingsSection
        title="Active agent"
        description="Integral AI is included and always active. No harness configuration is required."
      >
        <div className="flex flex-col gap-3">
          <ProviderGroup provider={PROVIDERS[0]} />
          <AdvancedHarnessIds />
        </div>
      </SettingsSection>

      <ConnectedAgentsBlock />
    </div>
  );
}
