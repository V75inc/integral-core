import { useMemo, useState } from 'react';
import { Cloud, Eye, Monitor, ShieldCheck } from 'lucide-react';

import { Button } from '../../../components/ui/Button';
import { Input, Surface, Text } from '../../../ui';
import { SettingsSection, StatusPill, ToggleRow } from '../components/Field';

interface ComputerUseApp {
  pid?: number;
  name: string;
  bundleId?: string;
  executable?: string;
}

interface ComputerUseConfig {
  bundled: boolean;
  active: boolean;
  grantId?: string | null;
  expiresAt?: string | null;
  durationMinutes?: number | null;
  screenshotsAllowed?: boolean;
  actionsAllowed?: boolean;
  apps?: Array<Pick<ComputerUseApp, 'name' | 'bundleId' | 'executable'>>;
  bindingGeneration?: string | null;
  jevEnabled?: boolean;
  jevConfigured?: boolean;
}

interface ComputerUseBridge {
  isDesktop: boolean;
  getComputerUseConfig: () => ComputerUseConfig;
  listComputerUseApps: () => Promise<{
    ok: boolean;
    code?: string;
    message?: string;
    apps: ComputerUseApp[];
  }>;
  approveComputerUse: (proposal: {
    apps: Array<Pick<ComputerUseApp, 'pid' | 'name' | 'bundleId' | 'executable'>>;
    durationMinutes: number;
    screenshotsAllowed: boolean;
    actionsAllowed: boolean;
  }) => Promise<{
    ok: boolean;
    code?: string;
    message?: string;
    grantId?: string;
    expiresAt?: string;
    durationMinutes?: number;
    screenshotsAllowed?: boolean;
    actionsAllowed?: boolean;
    apps?: Array<Pick<ComputerUseApp, 'name' | 'bundleId' | 'executable'>>;
    runtimeGeneration?: number;
  }>;
  stopComputerUse: () => Promise<{ ok: boolean }>;
  setComputerUseJev?: (patch: {
    enabled?: boolean;
    apiKey?: string;
    clearApiKey?: boolean;
  }) => Promise<{
    ok: boolean;
    code?: string;
    message?: string;
    jevEnabled?: boolean;
    jevConfigured?: boolean;
  }>;
}

function bridge(): ComputerUseBridge | null {
  return (
    window as Window & { integralDesktop?: ComputerUseBridge }
  ).integralDesktop ?? null;
}

function identity(app: Pick<ComputerUseApp, 'name' | 'bundleId' | 'executable' | 'pid'>): string {
  return app.bundleId || app.executable || `${app.pid ?? 'app'}:${app.name}`;
}

function durationLabel(minutes: number | null | undefined): string {
  if (!minutes) return '';
  if (minutes < 60) return `${minutes} minutes`;
  if (minutes === 60) return '1 hour';
  if (minutes % 60 === 0) return `${minutes / 60} hours`;
  return `${minutes} minutes`;
}

export function ComputerUseSection() {
  const desktop = bridge();
  const [config, setConfig] = useState<ComputerUseConfig>(() =>
    desktop?.getComputerUseConfig() ?? {
      bundled: false,
      active: false,
      apps: [],
      durationMinutes: 30,
      bindingGeneration: null,
    },
  );
  const [apps, setApps] = useState<ComputerUseApp[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [durationMinutes, setDurationMinutes] = useState(
    () => config.durationMinutes || 30,
  );
  const [screenshotsAllowed, setScreenshotsAllowed] = useState(
    () => config.screenshotsAllowed !== false,
  );
  const [actionsAllowed, setActionsAllowed] = useState(
    () => config.actionsAllowed === true,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jevEnabled, setJevEnabled] = useState(() => config.jevEnabled === true);
  const [jevKey, setJevKey] = useState('');
  const [jevMessage, setJevMessage] = useState<string | null>(null);

  const selectedApps = useMemo(
    () => apps.filter(app => selected.has(identity(app))),
    [apps, selected],
  );

  async function discover() {
    if (!desktop) return;
    setBusy(true);
    setError(null);
    try {
      const result = await desktop.listComputerUseApps();
      if (!result.ok) {
        setError(result.message || 'Connect Integral Desktop before choosing applications.');
        return;
      }
      setApps(result.apps);
      const remembered = new Set((config.apps || []).map(identity));
      setSelected(
        new Set(
          result.apps
            .filter(app => remembered.has(identity(app)))
            .map(identity),
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!desktop || selectedApps.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await desktop.approveComputerUse({
        apps: selectedApps.flatMap(({ pid, name, bundleId, executable }) => {
          if (!pid) return [];
          return [{
            pid,
            name,
            ...(bundleId ? { bundleId } : {}),
            ...(executable ? { executable } : {}),
          }];
        }),
        durationMinutes,
        screenshotsAllowed,
        actionsAllowed,
      });
      if (!result.ok) {
        setError(result.message || 'Computer-use access was not approved.');
        return;
      }
      setConfig({
        bundled: true,
        active: true,
        grantId: result.grantId,
        expiresAt: result.expiresAt,
        durationMinutes: result.durationMinutes ?? durationMinutes,
        screenshotsAllowed: result.screenshotsAllowed ?? screenshotsAllowed,
        actionsAllowed: result.actionsAllowed ?? actionsAllowed,
        apps: result.apps?.length
          ? result.apps
          : selectedApps.map(({ name, bundleId, executable }) => ({
              name,
              ...(bundleId ? { bundleId } : {}),
              ...(executable ? { executable } : {}),
            })),
        bindingGeneration: config.bindingGeneration,
      });
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!desktop) return;
    setBusy(true);
    try {
      await desktop.stopComputerUse();
      setConfig(current => ({ ...current, active: false, grantId: null, expiresAt: null }));
      setSelected(new Set());
    } finally {
      setBusy(false);
    }
  }

  async function saveJev() {
    if (!desktop?.setComputerUseJev) return;
    if (jevEnabled && !config.jevConfigured && !jevKey.trim()) {
      setError('Paste a TypeSafe API key before enabling Jev.');
      return;
    }
    setBusy(true);
    setError(null);
    setJevMessage(null);
    try {
      const result = await desktop.setComputerUseJev({
        enabled: jevEnabled,
        ...(jevKey.trim() ? { apiKey: jevKey.trim() } : {}),
      });
      if (!result.ok) {
        setError(result.message || 'Jev settings were not saved.');
        return;
      }
      setJevKey('');
      setConfig(current => ({
        ...current,
        jevEnabled: result.jevEnabled === true,
        jevConfigured: result.jevConfigured === true,
      }));
      setJevMessage(
        result.jevEnabled
          ? 'Jev will choose bounded native actions using the saved TypeSafe key.'
          : 'Jev is off. The resident continues to choose actions itself.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <Text variant="heading-md" weight="semibold" as="h2">
            Computer use
          </Text>
          <StatusPill state={config.active ? 'ok' : 'idle'}>
            {config.active ? 'Active on this device' : 'Off'}
          </StatusPill>
        </div>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Let the resident observe selected native applications through this desktop app,
          and optionally click and type in the background.
        </Text>
      </div>

      {!desktop?.isDesktop ? (
        <SettingsSection
          title="Open Integral Desktop"
          description="Computer use is available only from the installed desktop application."
        >
          <div />
        </SettingsSection>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            <Surface padding="md" tone="panel-2" border="subtle">
              <div className="flex items-start gap-3">
                <ShieldCheck size={18} aria-hidden="true" />
                <div>
                  <Text variant="heading-sm" weight="semibold" as="h3">
                    Stays on this device
                  </Text>
                  <Text variant="body-sm" tone="muted" as="p" className="mt-1">
                    The Cua runtime, its bounded manifest, raw protocol, and full desktop
                    access. The browser and unselected applications remain outside scope.
                  </Text>
                </div>
              </div>
            </Surface>
            <Surface padding="md" tone="panel-2" border="subtle">
              <div className="flex items-start gap-3">
                <Cloud size={18} aria-hidden="true" />
                <div>
                  <Text variant="heading-sm" weight="semibold" as="h3">
                    May leave this device
                  </Text>
                  <Text variant="body-sm" tone="muted" as="p" className="mt-1">
                    Window titles and accessibility text for approved apps. Window screenshots
                    leave only when you enable them below.
                  </Text>
                </div>
              </div>
            </Surface>
          </div>

          <SettingsSection
            title="Temporary application access"
            description="Choose exact applications, review the native disclosure, and approve a time-limited lease. Integral Desktop remembers the last approval on this device until it expires or you revoke it."
          >
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  icon={<Monitor size={14} />}
                  onClick={() => void discover()}
                  disabled={busy || config.active || !config.bundled}
                >
                  Choose applications
                </Button>
                {config.active ? (
                  <Button
                    type="button"
                    variant="danger"
                    onClick={() => void revoke()}
                    disabled={busy}
                  >
                    Revoke now
                  </Button>
                ) : null}
              </div>

              {!config.bundled ? (
                <div role="alert">
                  <Text variant="body-sm" tone="danger" as="p">
                    This desktop build is missing its bundled computer-use runtime.
                  </Text>
                </div>
              ) : null}

              {apps.length > 0 ? (
                <fieldset className="flex flex-col gap-2">
                  <Text variant="label" tone="subtle" as="legend">
                    Running applications
                  </Text>
                  {apps.map(app => {
                    const key = identity(app);
                    return (
                      <label key={key} className="flex cursor-pointer items-center gap-3 py-1">
                        <input
                          type="checkbox"
                          checked={selected.has(key)}
                          onChange={event => {
                            setSelected(current => {
                              const next = new Set(current);
                              if (event.target.checked) next.add(key);
                              else next.delete(key);
                              return next;
                            });
                          }}
                        />
                        <Text variant="body" as="span">{app.name}</Text>
                      </label>
                    );
                  })}
                </fieldset>
              ) : null}

              {selectedApps.length > 0 && !config.active ? (
                <div className="flex flex-col gap-3">
                  <label className="flex flex-col gap-1">
                    <Text variant="label" tone="subtle" as="span">Access duration</Text>
                    <select
                      aria-label="Access duration"
                      value={durationMinutes}
                      onChange={event => setDurationMinutes(Number(event.target.value))}
                      className="min-h-10 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--bg)] px-3 text-[var(--text)]"
                    >
                      <option value={15}>15 minutes</option>
                      <option value={30}>30 minutes</option>
                      <option value={60}>1 hour</option>
                      <option value={240}>4 hours</option>
                    </select>
                  </label>
                  <label className="flex cursor-pointer items-start gap-3">
                    <input
                      type="checkbox"
                      checked={screenshotsAllowed}
                      onChange={event => setScreenshotsAllowed(event.target.checked)}
                    />
                    <span>
                      <Text variant="body" weight="medium" as="span">
                        Allow selected-window screenshots
                      </Text>
                      <Text variant="body-sm" tone="muted" as="span" className="mt-0.5 block">
                        Screenshots are size-limited, sent over an encrypted artifact channel,
                        and expire by default.
                      </Text>
                    </span>
                  </label>
                  <label className="flex cursor-pointer items-start gap-3">
                    <input
                      type="checkbox"
                      checked={actionsAllowed}
                      onChange={event => setActionsAllowed(event.target.checked)}
                    />
                    <span>
                      <Text variant="body" weight="medium" as="span">
                        Allow background clicks and typing
                      </Text>
                      <Text variant="body-sm" tone="muted" as="span" className="mt-0.5 block">
                        The resident may click, type, press keys, and send hotkeys in approved
                        apps without bringing them forward. Foreground control stays off.
                      </Text>
                    </span>
                  </label>
                  <Button
                    type="button"
                    variant="primary"
                    icon={<Eye size={14} />}
                    onClick={() => void approve()}
                    disabled={busy}
                  >
                    Approve access
                  </Button>
                </div>
              ) : null}

              {config.apps && config.apps.length > 0 ? (
                <div className="flex flex-col gap-1">
                  <Text variant="label" tone="subtle" as="p">
                    {config.active ? 'Approved applications' : 'Last approved applications'}
                  </Text>
                  <ul className="flex flex-col gap-1">
                    {config.apps.map(app => (
                      <li key={identity(app)}>
                        <Text variant="body" as="span">{app.name}</Text>
                      </li>
                    ))}
                  </ul>
                  <Text variant="body-sm" tone="muted" as="p">
                    {config.active && config.expiresAt
                      ? `Access lasts ${durationLabel(config.durationMinutes)} and expires ${new Date(config.expiresAt).toLocaleString()}. Disconnecting or quitting Integral Desktop does not forget this approval.`
                      : `Last approval was ${durationLabel(config.durationMinutes) || 'time-limited'}. Choose applications again to start a new lease.`}
                  </Text>
                </div>
              ) : null}
              {error ? (
                <div role="alert">
                  <Text variant="body-sm" tone="danger" as="p">
                    {error}
                  </Text>
                </div>
              ) : null}
            </div>
          </SettingsSection>

          {desktop.setComputerUseJev ? (
            <SettingsSection
              title="Jev action chooser"
              description="TypeSafe Jev picks the next bounded click or type from the accessibility tree. Integral still executes through Cua Driver. Get a key from the TypeSafe console. The key stays in this device's OS keychain and is never sent to Integral."
            >
              <div className="flex flex-col gap-4">
                <ToggleRow
                  checked={jevEnabled}
                  onChange={setJevEnabled}
                  label="Enable Jev for native apps"
                  hint="Sends compact control labels and candidate IDs to api.typesafe.ai. Screenshots and element tokens stay on this device."
                />
                <label className="flex flex-col gap-1">
                  <Text variant="label" tone="subtle" as="span">
                    TypeSafe API key
                  </Text>
                  <Input
                    type="password"
                    value={jevKey}
                    onChange={event => setJevKey(event.target.value)}
                    placeholder={
                      config.jevConfigured
                        ? 'Key saved on this device — paste to replace'
                        : 'Paste a TypeSafe API key'
                    }
                    autoComplete="off"
                    aria-label="TypeSafe API key"
                  />
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => void saveJev()}
                    disabled={busy}
                  >
                    Save Jev settings
                  </Button>
                  <StatusPill state={config.jevEnabled && config.jevConfigured ? 'ok' : 'idle'}>
                    {config.jevEnabled && config.jevConfigured ? 'Jev on' : 'Jev off'}
                  </StatusPill>
                </div>
                {jevMessage ? (
                  <Text variant="body-sm" tone="muted" as="p">
                    {jevMessage}
                  </Text>
                ) : null}
              </div>
            </SettingsSection>
          ) : null}
        </>
      )}
    </div>
  );
}
