/**
 * AppUninstallModal — uninstall flow UI with dependent / cross-App warnings.
 *
 * Phase 10 Plan 10-05 (APP-LIFECYCLE-01). Two-mode UI:
 *
 *  1. Normal mode — submits ``POST /api/apps/{id}/uninstall`` without
 *     ``force``. If the backend returns 409 ``app_uninstall_blocked``,
 *     surface the blocking dependents + reference list and reveal a
 *     "Force uninstall" override button.
 *  2. Force mode — submits ``POST /api/apps/{id}/uninstall?force=true``.
 *     Emits a single ``app.force_uninstalled`` ChangeEvent server-side.
 *     The button carries a prominent warning ("this will break
 *     referencing data").
 *
 * Migrated to FormDialog template (Phase 6) — multi-phase UI rendered via
 * `actions` override per phase. Phase-specific data-testid attrs on the
 * Confirm/Force buttons are preserved.
 */

import { useState } from 'react';
import { Button } from '../ui';
import { Text } from '../../ui';
import { FormDialog } from '../../templates';
import { appsApi } from '../../api/apps';

interface AppUninstallModalProps {
  open: boolean;
  onClose(): void;
  appId: string;
  appName: string;
  /** Called with the uninstalled App id on success. */
  onUninstalled(appId: string): void;
}

interface BlockingDependent {
  app_id: string;
  app_name: string;
  dep_key: string;
}

interface BlockingReference {
  source_app_id: string;
  source_app_name: string;
  source_entry_id: string;
  source_track_id: string;
  relation_field_key: string;
}

type Phase = 'confirm' | 'uninstalling' | 'force_confirm' | 'queued';

export function AppUninstallModal(props: AppUninstallModalProps) {
  const { open, onClose, appId, appName, onUninstalled } = props;
  const [phase, setPhase] = useState<Phase>('confirm');
  const [error, setError] = useState<string | null>(null);
  const [blockingDeps, setBlockingDeps] = useState<BlockingDependent[]>([]);
  const [blockingRefs, setBlockingRefs] = useState<BlockingReference[]>([]);

  async function handleUninstall(force: boolean) {
    setPhase('uninstalling');
    setError(null);
    try {
      const result = await appsApi.uninstall(appId, { force });
      if (result.status === 'queued') {
        setPhase('queued');
      } else if (
        result.status === 'uninstalled' ||
        result.status === 'force_uninstalled'
      ) {
        onUninstalled(result.app_id);
        onClose();
      } else {
        setError(`Unexpected uninstall status: ${result.status}`);
        setPhase('confirm');
      }
    } catch (e) {
      const err = e as {
        response?: {
          status?: number;
          data?: {
            error_code?: string;
            message?: string;
            details?: {
              blocking_dependents?: BlockingDependent[];
              blocking_references?: BlockingReference[];
            };
          };
        };
      };
      const payload = err.response?.data;
      if (
        err.response?.status === 409 &&
        payload?.error_code === 'app_uninstall_blocked'
      ) {
        setBlockingDeps(payload.details?.blocking_dependents || []);
        setBlockingRefs(payload.details?.blocking_references || []);
        setError(payload.message || 'Uninstall blocked by dependents.');
        setPhase('force_confirm');
      } else {
        setError(payload?.message || 'Uninstall failed');
        setPhase('confirm');
      }
    }
  }

  if (phase === 'confirm') {
    return (
      <FormDialog
        open={open}
        onClose={onClose}
        title={`Uninstall ${appName}`}
        actions={
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              onClick={() => handleUninstall(false)}
              data-testid="uninstall-confirm"
            >
              Uninstall
            </Button>
          </>
        }
      >
        <Text variant="body" as="p">
          Uninstalling an App archives its Tracks by default. Entries are
          preserved; agents and scheduled runs are unregistered.
        </Text>
        {error && (
          <Text variant="body" tone="danger" as="p" data-testid="uninstall-error">
            {error}
          </Text>
        )}
      </FormDialog>
    );
  }

  if (phase === 'force_confirm') {
    return (
      <FormDialog
        open={open}
        onClose={onClose}
        title={`Uninstall ${appName}`}
        actions={
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              onClick={() => handleUninstall(true)}
              data-testid="uninstall-force"
              variant="danger"
            >
              Force uninstall
            </Button>
          </>
        }
      >
        {blockingDeps.length > 0 && (
          <div
            className="rounded-md border border-red-500 bg-red-50/20 px-3 py-2 text-sm"
            data-testid="blocking-deps-banner"
          >
            <Text variant="body-sm" weight="medium" as="p" className="mb-1">
              Uninstall blocked by dependent Apps:
            </Text>
            <ul className="list-disc ml-5 text-xs">
              {blockingDeps.map(d => (
                <li key={d.app_id}>
                  <Text variant="body-sm" weight="medium">{d.app_name}</Text>{' '}
                  <Text variant="body-sm" tone="subtle">
                    (depends on: {d.dep_key})
                  </Text>
                </li>
              ))}
            </ul>
          </div>
        )}

        {blockingRefs.length > 0 && (
          <div
            className="rounded-md border border-amber-500 bg-amber-50/20 px-3 py-2 text-sm"
            data-testid="blocking-refs-banner"
          >
            <Text variant="body-sm" weight="medium" as="p" className="mb-1">
              Cross-App references point at this App ({blockingRefs.length}):
            </Text>
            <ul className="list-disc ml-5 text-xs space-y-0.5">
              {Object.entries(
                blockingRefs.reduce<Record<string, number>>((acc, r) => {
                  const key = r.source_app_name || r.source_app_id;
                  acc[key] = (acc[key] || 0) + 1;
                  return acc;
                }, {}),
              ).map(([refAppName, count]) => (
                <li key={refAppName}>
                  <Text variant="body-sm" weight="medium">{refAppName}</Text>:{' '}
                  <Text variant="body-sm" tone="subtle">
                    {count} reference{count !== 1 ? 's' : ''}
                  </Text>
                </li>
              ))}
            </ul>
          </div>
        )}

        <Text variant="body-sm" tone="subtle" as="p" className="mt-2">
          Uninstall the dependents first, or force-uninstall — which will
          break referencing data and emit a prominent{' '}
          <code className="font-mono">app.force_uninstalled</code> audit
          event.
        </Text>
      </FormDialog>
    );
  }

  if (phase === 'queued') {
    return (
      <FormDialog
        open={open}
        onClose={onClose}
        title={`Uninstall ${appName}`}
        actions={
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        }
      >
        <Text variant="body" tone="subtle" as="p">
          Uninstall has been queued. This App will remain visible until its
          lifecycle work completes.
        </Text>
      </FormDialog>
    );
  }

  // phase === 'uninstalling'
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title={`Uninstall ${appName}`}
      noActions
    >
      <Text variant="body" tone="subtle" as="p">
        Uninstalling…
      </Text>
    </FormDialog>
  );
}
