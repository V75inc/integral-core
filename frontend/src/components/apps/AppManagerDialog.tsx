/**
 * App Manager — install / uninstall library app bundles in one dialog.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Check, ExternalLink, Loader2, Package } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui';
import { Input, Surface, Text, Textarea } from '../../ui';
import { IncludeSeedDataToggle } from './IncludeSeedDataToggle';
import { countManifestSeedEntries } from '../../utils/manifestSeeds';
import {
  appsApi,
  type BatchInstallResponse,
} from '../../api/apps';
import { contentProfilesApi } from '../../api/contentProfiles';
import type { App, ContentProfileNode } from '../../types';
import { summarizeLibraryManifest } from '../../lib/contentProfileManifest';
import {
  extractPackageMeta,
  filterAppScopedLibraryPackages,
  isBundleBackedApp,
  isPackageInstalled,
  lifecycleBadge,
} from './appBundleMatching';
import {
  AppSettingsFinalizeStep,
  type PendingSettingsInstall,
} from './AppSettingsFinalizeStep';
import { AppUninstallModal } from './AppUninstallModal';
import { useAuth } from '../../context/AuthContext';
import { isSamePrincipal } from '../../utils';

const LINE_STROKE = 1.5;

interface SelectedInstallRow {
  library_cp_id: string;
  name: string;
  description: string;
  default_name: string;
  default_description: string;
}

export interface AppManagerDialogProps {
  isOpen: boolean;
  onClose: () => void;
  apps: App[];
  onChanged?: () => void;
  onCreateBlankApp?: () => void;
}

interface ManagerResults {
  batch?: BatchInstallResponse;
  uninstalled: Array<{ app_id: string; name: string }>;
  uninstallFailed: Array<{ app_id: string; name: string; error: string }>;
  uninstallBlocked: Array<{ app_id: string; name: string }>;
}

type DialogPhase = 'manage' | 'settings' | 'results';

export function AppManagerDialog({
  isOpen,
  onClose,
  apps,
  onChanged,
  onCreateBlankApp,
}: AppManagerDialogProps) {
  const { user } = useAuth();
  const [profiles, setProfiles] = useState<ContentProfileNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<DialogPhase>('manage');
  const [selectedInstall, setSelectedInstall] = useState<
    Map<string, SelectedInstallRow>
  >(new Map());
  const [selectedUninstall, setSelectedUninstall] = useState<Set<string>>(
    new Set(),
  );
  const [submitting, setSubmitting] = useState(false);
  const [includeSeedData, setIncludeSeedData] = useState(true);
  const [results, setResults] = useState<ManagerResults | null>(null);
  const [settingsQueue, setSettingsQueue] = useState<PendingSettingsInstall[]>(
    [],
  );
  const [settingsIndex, setSettingsIndex] = useState(0);
  const [blockedUninstall, setBlockedUninstall] = useState<{
    appId: string;
    appName: string;
  } | null>(null);

  const bundleApps = useMemo(
    () =>
      apps.filter(
        a =>
          isBundleBackedApp(a) &&
          a.lifecycle_state !== 'uninstalled',
      ),
    [apps],
  );

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResults(null);
    setPhase('manage');
    setSelectedInstall(new Map());
    setSelectedUninstall(new Set());
    setIncludeSeedData(true);
    setSettingsQueue([]);
    setSettingsIndex(0);
    setBlockedUninstall(null);
    (async () => {
      try {
        const data = await contentProfilesApi.list();
        if (cancelled) return;
        const libs = filterAppScopedLibraryPackages(data);
        libs.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
        setProfiles(libs);
      } catch (err) {
        if (cancelled) return;
        setError(
          (err as { message?: string })?.message ||
            'Failed to load library packages.',
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  const toggleInstall = (profile: ContentProfileNode) => {
    if (isPackageInstalled(profile, apps)) return;
    setSelectedInstall(prev => {
      const next = new Map(prev);
      if (next.has(profile.id)) {
        next.delete(profile.id);
      } else {
        const { name, description } = extractPackageMeta(profile);
        next.set(profile.id, {
          library_cp_id: profile.id,
          name,
          description,
          default_name: name,
          default_description: description,
        });
      }
      return next;
    });
  };

  const updateInstallRow = (
    library_cp_id: string,
    patch: Partial<SelectedInstallRow>,
  ) => {
    setSelectedInstall(prev => {
      const next = new Map(prev);
      const cur = next.get(library_cp_id);
      if (!cur) return prev;
      next.set(library_cp_id, { ...cur, ...patch });
      return next;
    });
  };

  const toggleUninstall = (app: App) => {
    if (!canUninstallApp(app, user)) return;
    setSelectedUninstall(prev => {
      const next = new Set(prev);
      if (next.has(app.id)) next.delete(app.id);
      else next.add(app.id);
      return next;
    });
  };

  const selectedInstallCount = selectedInstall.size;
  const selectedUninstallCount = selectedUninstall.size;
  const selectedSeedEntryCount = useMemo(() => {
    let total = 0;
    for (const row of selectedInstall.values()) {
      const profile = profiles.find(p => p.id === row.library_cp_id);
      total += countManifestSeedEntries(profile?.manifest || {});
    }
    return total;
  }, [profiles, selectedInstall]);

  const applyLabel = submitting
    ? 'Applying…'
    : selectedInstallCount + selectedUninstallCount === 0
      ? 'Apply changes'
      : `Apply changes (${selectedInstallCount + selectedUninstallCount})`;

  async function resumeSettingsForApp(app: App): Promise<PendingSettingsInstall | null> {
    const libraryId = app.installed_from_library_id;
    if (!libraryId) return null;
    try {
      const batch = await appsApi.batchInstall(
        [{ library_cp_id: libraryId }],
        { include_seed_data: includeSeedData },
      );
      const row = batch.installed.find(i => i.app_id === app.id);
      if (
        row?.status === 'awaiting_settings' &&
        row.install_token &&
        row.settings_schema
      ) {
        return {
          appId: app.id,
          appName: app.name,
          installToken: row.install_token,
          settingsSchema: row.settings_schema,
        };
      }
    } catch {
      // fall through to settings endpoint
    }
    try {
      const settingsRes = await appsApi.getAppSettings(app.id);
      const batch = await appsApi.batchInstall([{ library_cp_id: libraryId }]);
      const row = batch.installed.find(i => i.app_id === app.id);
      if (row?.install_token && settingsRes.settings_schema) {
        return {
          appId: app.id,
          appName: app.name,
          installToken: row.install_token,
          settingsSchema: settingsRes.settings_schema,
        };
      }
    } catch {
      return null;
    }
    return null;
  }

  async function applyChanges() {
    if (selectedInstallCount === 0 && selectedUninstallCount === 0) return;
    setSubmitting(true);
    setError(null);
    const outcome: ManagerResults = {
      uninstalled: [],
      uninstallFailed: [],
      uninstallBlocked: [],
    };
    const pendingSettings: PendingSettingsInstall[] = [];

    try {
      for (const appId of selectedUninstall) {
        const app = bundleApps.find(a => a.id === appId);
        if (!app) continue;
        try {
          const res = await appsApi.uninstall(appId);
          if (
            res.status === 'uninstalled' ||
            res.status === 'force_uninstalled'
          ) {
            outcome.uninstalled.push({ app_id: appId, name: app.name });
          }
        } catch (e) {
          const err = e as {
            response?: {
              status?: number;
              data?: { error_code?: string; message?: string };
            };
          };
          if (
            err.response?.status === 409 &&
            err.response?.data?.error_code === 'app_uninstall_blocked'
          ) {
            outcome.uninstallBlocked.push({ app_id: appId, name: app.name });
            setBlockedUninstall({ appId, appName: app.name });
          } else {
            outcome.uninstallFailed.push({
              app_id: appId,
              name: app.name,
              error:
                err.response?.data?.message || 'Uninstall failed',
            });
          }
        }
      }

      if (selectedInstallCount > 0) {
        const items = Array.from(selectedInstall.values()).map(row => ({
          library_cp_id: row.library_cp_id,
          name: row.name !== row.default_name ? row.name : undefined,
          description:
            row.description !== row.default_description
              ? row.description
              : undefined,
        }));
        outcome.batch = await appsApi.batchInstall(items, {
          include_seed_data: includeSeedData,
        });
        for (const row of outcome.batch.installed) {
          if (
            row.status === 'awaiting_settings' &&
            row.app_id &&
            row.install_token &&
            row.settings_schema
          ) {
            pendingSettings.push({
              appId: row.app_id,
              appName: row.name || row.library_cp_id,
              installToken: row.install_token,
              settingsSchema: row.settings_schema,
            });
          }
        }
      }

      if (pendingSettings.length > 0) {
        setResults(outcome);
        setSettingsQueue(pendingSettings);
        setSettingsIndex(0);
        setPhase('settings');
        onChanged?.();
        return;
      }

      setResults(outcome);
      setPhase('results');
      onChanged?.();
    } catch (err) {
      setError(
        (err as { message?: string })?.message || 'Failed to apply changes.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  function handleSettingsComplete() {
    const nextIndex = settingsIndex + 1;
    if (nextIndex < settingsQueue.length) {
      setSettingsIndex(nextIndex);
      return;
    }
    setPhase('results');
    onChanged?.();
  }

  async function openSettingsForApp(app: App) {
    setSubmitting(true);
    setError(null);
    try {
      const pending = await resumeSettingsForApp(app);
      if (!pending) {
        setError('Could not resume install for this app.');
        return;
      }
      setSettingsQueue([pending]);
      setSettingsIndex(0);
      setPhase('settings');
    } finally {
      setSubmitting(false);
    }
  }

  const title =
    phase === 'settings'
      ? 'Finish app setup'
      : phase === 'results'
        ? 'Changes applied'
        : 'Manage apps';

  return (
    <>
      <Modal open={isOpen} onClose={onClose} title={title} width="max-w-dialog-wide">
        {phase === 'settings' && settingsQueue[settingsIndex] ? (
          <AppSettingsFinalizeStep
            pending={settingsQueue[settingsIndex]}
            onComplete={() => handleSettingsComplete()}
            onFinishLater={() => {
              setPhase(results ? 'results' : 'manage');
            }}
            onError={() => {}}
          />
        ) : phase === 'results' && results ? (
          <ResultsView results={results} onClose={onClose} />
        ) : (
          <>
            <Modal.Body>
              <div className="space-y-6" data-testid="app-manager-dialog">
                <Text variant="body-sm" tone="subtle">
                  Install available app packages or uninstall bundle-backed apps
                  in this workspace.
                </Text>

                {error && (
                  <div
                    role="alert"
                    data-testid="app-manager-error"
                    className="rounded-[var(--radius-card)] border border-[color:var(--danger-fg)]/30 bg-[color:var(--danger-fg)]/10 px-4 py-3"
                  >
                    <Text variant="body-sm" tone="danger">
                      {error}
                    </Text>
                  </div>
                )}

                {loading ? (
                  <div
                    className="flex items-center gap-2"
                    data-testid="app-manager-loading"
                  >
                    <Loader2
                      size={14}
                      strokeWidth={LINE_STROKE}
                      className="animate-spin"
                    />
                    <Text variant="body-sm" tone="muted">
                      Loading…
                    </Text>
                  </div>
                ) : (
                  <>
                    <section>
                      <Text
                        as="h3"
                        variant="meta"
                        weight="semibold"
                        className="mb-2 uppercase tracking-wide"
                      >
                        Installed
                      </Text>
                      {bundleApps.length === 0 ? (
                        <Surface
                          tone="panel-2"
                          border="subtle"
                          radius="card"
                          padding="md"
                        >
                          <Text variant="body-sm" tone="muted">
                            No bundle-backed apps in this workspace.
                          </Text>
                        </Surface>
                      ) : (
                        <ul className="space-y-2" data-testid="app-manager-installed">
                          {bundleApps.map(app => (
                            <InstalledRow
                              key={app.id}
                              app={app}
                              selected={selectedUninstall.has(app.id)}
                              canUninstall={canUninstallApp(app, user)}
                              onToggle={() => toggleUninstall(app)}
                              onCompleteSetup={() => openSettingsForApp(app)}
                              disabled={submitting}
                            />
                          ))}
                        </ul>
                      )}
                    </section>

                    <section>
                      <Text
                        as="h3"
                        variant="meta"
                        weight="semibold"
                        className="mb-2 uppercase tracking-wide"
                      >
                        Available
                      </Text>
                      {profiles.length === 0 ? (
                        <Surface
                          tone="panel-2"
                          border="subtle"
                          radius="card"
                          padding="md"
                        >
                          <Text variant="body-sm" tone="muted">
                            No app packages available.
                          </Text>
                        </Surface>
                      ) : (
                        <ul
                          className="space-y-2 max-h-[22rem] overflow-y-auto pr-1"
                          data-testid="app-manager-available"
                        >
                          {profiles.map(profile => {
                            const { name, description, slug } =
                              extractPackageMeta(profile);
                            const installed = isPackageInstalled(profile, apps);
                            const isSelected = selectedInstall.has(profile.id);
                            const row = selectedInstall.get(profile.id);
                            const summary = summarizeLibraryManifest(
                              profile.manifest,
                            );
                            return (
                              <li
                                key={profile.id}
                                data-testid={`app-manager-row-${slug || profile.id}`}
                              >
                                <AvailableRow
                                  name={name}
                                  description={description}
                                  slug={slug}
                                  installed={installed}
                                  isSelected={isSelected}
                                  summary={summary}
                                  row={row}
                                  disabled={installed || submitting}
                                  onToggle={() => toggleInstall(profile)}
                                  onUpdate={patch =>
                                    updateInstallRow(profile.id, patch)
                                  }
                                />
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </section>

                    {selectedInstallCount > 0 && (
                      <Surface
                        tone="panel-2"
                        border="subtle"
                        radius="card"
                        padding="md"
                      >
                        <IncludeSeedDataToggle
                          checked={includeSeedData}
                          onChange={setIncludeSeedData}
                          seedEntryCount={selectedSeedEntryCount}
                          disabled={submitting}
                        />
                      </Surface>
                    )}
                  </>
                )}
              </div>
            </Modal.Body>

            <Modal.Footer align="between">
              <div className="flex items-center gap-3">
                <Button variant="ghost" onClick={onClose} disabled={submitting}>
                  Cancel
                </Button>
                {onCreateBlankApp ? (
                  <button
                    type="button"
                    className="text-sm text-[var(--link)] hover:underline disabled:opacity-50"
                    onClick={onCreateBlankApp}
                    disabled={submitting}
                    data-testid="app-manager-blank-app"
                  >
                    Create blank app…
                  </button>
                ) : null}
              </div>
              <Button
                variant="primary"
                icon={<Package size={14} strokeWidth={LINE_STROKE} />}
                onClick={applyChanges}
                loading={submitting}
                disabled={
                  submitting ||
                  (selectedInstallCount === 0 && selectedUninstallCount === 0)
                }
                data-testid="app-manager-apply"
              >
                {applyLabel}
              </Button>
            </Modal.Footer>
          </>
        )}
      </Modal>

      {blockedUninstall ? (
        <AppUninstallModal
          open
          appId={blockedUninstall.appId}
          appName={blockedUninstall.appName}
          onClose={() => setBlockedUninstall(null)}
          onUninstalled={() => {
            setBlockedUninstall(null);
            onChanged?.();
          }}
        />
      ) : null}
    </>
  );
}

function canUninstallApp(app: App, user: { id: string; user_id?: string } | null): boolean {
  if (!user || !app.owner_user_id) return false;
  return isSamePrincipal(user, app.owner_user_id);
}

function InstalledRow({
  app,
  selected,
  canUninstall,
  onToggle,
  onCompleteSetup,
  disabled,
}: {
  app: App;
  selected: boolean;
  canUninstall: boolean;
  onToggle: () => void;
  onCompleteSetup: () => void;
  disabled: boolean;
}) {
  const badge = lifecycleBadge(app.lifecycle_state);
  const needsSettings = app.lifecycle_state === 'awaiting_settings';

  return (
    <Surface
      tone="panel-2"
      border="subtle"
      radius="card"
      className={selected ? 'border-[var(--brand-accent-line)] bg-[var(--brand-accent-soft)]' : ''}
    >
      <div className="flex items-start gap-3 px-3 py-2.5">
        {canUninstall ? (
          <Checkbox checked={selected} onChange={onToggle} disabled={disabled} />
        ) : (
          <span className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Text as="span" variant="body" weight="medium" truncate>
              {app.name}
            </Text>
            {badge ? <StatusBadge label={badge} variant={needsSettings ? 'warning' : 'default'} /> : null}
            {app.source_profile_slug ? (
              <Text as="span" variant="meta" tone="subtle" className="font-mono">
                {app.source_profile_slug}
              </Text>
            ) : null}
          </div>
          {app.description ? (
            <Text variant="meta" tone="subtle" className="mt-0.5 line-clamp-2">
              {app.description}
            </Text>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Link
              to={`/apps/${app.id}`}
              className="inline-flex items-center gap-1 text-xs text-[var(--link)] hover:underline"
            >
              Open app
              <ExternalLink size={12} strokeWidth={LINE_STROKE} />
            </Link>
            {needsSettings ? (
              <button
                type="button"
                className="text-xs text-[var(--link)] hover:underline"
                onClick={onCompleteSetup}
                disabled={disabled}
              >
                Complete setup
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </Surface>
  );
}

function AvailableRow({
  name,
  description,
  slug,
  installed,
  isSelected,
  summary,
  row,
  disabled,
  onToggle,
  onUpdate,
}: {
  name: string;
  description: string;
  slug: string;
  installed: boolean;
  isSelected: boolean;
  summary: ReturnType<typeof summarizeLibraryManifest>;
  row?: SelectedInstallRow;
  disabled: boolean;
  onToggle: () => void;
  onUpdate: (patch: Partial<SelectedInstallRow>) => void;
}) {
  const selectedShell = `
    rounded-[var(--radius-card)] border border-[var(--brand-accent-line)]
    bg-[var(--brand-accent-soft)]
  `;

  const header = (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      className="w-full text-left flex items-start gap-3 px-3 py-2.5 disabled:cursor-not-allowed"
    >
      {/* The checkbox stopPropagation()s its own click (so row-level
          handlers can't double-fire) — wiring a no-op here made the
          checkbox itself dead while only the row text toggled. */}
      <Checkbox checked={isSelected} onChange={onToggle} disabled={disabled} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Text as="span" variant="body" weight="medium" truncate>
            {name}
          </Text>
          {installed ? <StatusBadge label="Installed" variant="muted" /> : null}
          {slug ? (
            <Text as="span" variant="meta" tone="subtle" className="font-mono">
              {slug}
            </Text>
          ) : null}
        </div>
        {description ? (
          <Text variant="meta" tone="subtle" className="mt-0.5">
            {description}
          </Text>
        ) : null}
        <Text variant="meta" tone="subtle" className="mt-1">
          {summary.prescribedTrackCount} tracks · {summary.skillCount} skills ·{' '}
          {summary.agentCount} agents
        </Text>
      </div>
    </button>
  );

  if (isSelected && row) {
    return (
      <div className={`${selectedShell} transition-colors duration-fast`}>
        {header}
        <div className="border-t border-[var(--panel-border)] px-3 py-3 space-y-2">
          <Input
            value={row.name}
            onChange={e => onUpdate({ name: e.target.value })}
            placeholder="App name"
            aria-label="App name override"
            size="sm"
          />
          <Textarea
            value={row.description}
            onChange={e => onUpdate({ description: e.target.value })}
            placeholder="Description"
            aria-label="App description override"
            rows={2}
            size="sm"
          />
        </div>
      </div>
    );
  }

  return (
    <Surface
      tone="panel-2"
      border="subtle"
      radius="card"
      className={installed ? 'opacity-60' : ''}
    >
      {header}
    </Surface>
  );
}

function Checkbox({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
}) {
  return (
    <span
      role="checkbox"
      aria-checked={checked}
      tabIndex={disabled ? -1 : 0}
      onClick={e => {
        e.stopPropagation();
        if (!disabled) onChange();
      }}
      onKeyDown={e => {
        if (disabled) return;
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          onChange();
        }
      }}
      className={`
        mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center
        rounded-[3px] border-2 cursor-pointer
        ${checked
          ? 'border-[var(--brand-accent)] bg-[var(--brand-accent)]'
          : 'border-[var(--text-muted)]'
        }
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
      `}
    >
      {checked ? (
        <Check size={10} strokeWidth={3} className="text-white" />
      ) : null}
    </span>
  );
}

function StatusBadge({
  label,
  variant,
}: {
  label: string;
  variant: 'default' | 'warning' | 'muted';
}) {
  const borderClass =
    variant === 'warning'
      ? 'border-amber-500/40'
      : 'border-[var(--panel-border)]';
  const textTone =
    variant === 'warning' ? 'warn' : variant === 'muted' ? 'subtle' : 'muted';

  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded-[var(--radius-pill)] border ${borderClass}`}
    >
      <Text
        variant="meta"
        tone={textTone}
        weight="medium"
        className="uppercase tracking-wide"
      >
        {label}
      </Text>
    </span>
  );
}

function ResultsView({
  results,
  onClose,
}: {
  results: ManagerResults;
  onClose: () => void;
}) {
  const batch = results.batch;
  return (
    <>
      <Modal.Body>
        <div className="space-y-4" data-testid="app-manager-result">
          {results.uninstalled.length > 0 && (
            <ResultSection title={`Uninstalled (${results.uninstalled.length})`}>
              {results.uninstalled.map(row => (
                <li key={row.app_id} className="flex items-center gap-2">
                  <Check size={14} className="text-[var(--brand-accent)]" />
                  <Text variant="body">{row.name}</Text>
                </li>
              ))}
            </ResultSection>
          )}

          {results.uninstallBlocked.length > 0 && (
            <ResultSection
              title={`Uninstall blocked (${results.uninstallBlocked.length})`}
              tone="danger"
            >
              {results.uninstallBlocked.map(row => (
                <li key={row.app_id}>
                  <Text variant="body" tone="danger">
                    {row.name} — resolve dependents or force uninstall
                  </Text>
                </li>
              ))}
            </ResultSection>
          )}

          {results.uninstallFailed.length > 0 && (
            <ResultSection
              title={`Uninstall failed (${results.uninstallFailed.length})`}
              tone="danger"
            >
              {results.uninstallFailed.map(row => (
                <li key={row.app_id}>
                  <Text variant="body" tone="danger">
                    {row.name} — {row.error}
                  </Text>
                </li>
              ))}
            </ResultSection>
          )}

          {batch ? (
            <>
              <Surface tone="panel-2" border="default" radius="card" padding="md">
                <Text variant="body">
                  <Text as="span" weight="semibold">
                    {batch.installed.length}
                  </Text>{' '}
                  installed,{' '}
                  <Text as="span" weight="semibold">
                    {batch.skipped.length}
                  </Text>{' '}
                  skipped,{' '}
                  <Text as="span" weight="semibold" tone="danger">
                    {batch.failed.length}
                  </Text>{' '}
                  failed
                </Text>
              </Surface>
              {batch.installed.length > 0 && (
                <ResultSection title={`Installed (${batch.installed.length})`}>
                  {batch.installed.map(row => (
                    <li key={row.library_cp_id} className="flex items-center gap-2">
                      <Check size={14} className="text-[var(--brand-accent)]" />
                      <Text variant="body">
                        {row.name || row.library_cp_id}
                        {row.status === 'awaiting_settings'
                          ? ' (needs settings)'
                          : ''}
                      </Text>
                    </li>
                  ))}
                </ResultSection>
              )}
              {batch.failed.length > 0 && (
                <ResultSection title={`Install failed (${batch.failed.length})`} tone="danger">
                  {batch.failed.map(row => (
                    <li key={row.library_cp_id}>
                      <Text variant="body" tone="danger">
                        {row.name || row.library_cp_id} — {row.error}
                      </Text>
                    </li>
                  ))}
                </ResultSection>
              )}
            </>
          ) : null}
        </div>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="primary" onClick={onClose}>
          Close
        </Button>
      </Modal.Footer>
    </>
  );
}

function ResultSection({
  title,
  children,
  tone,
}: {
  title: string;
  children: ReactNode;
  tone?: 'danger';
}) {
  return (
    <details open>
      <summary className="cursor-pointer">
        <Text
          as="span"
          variant="meta"
          tone={tone === 'danger' ? 'danger' : 'muted'}
        >
          {title}
        </Text>
      </summary>
      <ul className="mt-2 space-y-1 pl-1">{children}</ul>
    </details>
  );
}
