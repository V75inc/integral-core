/**
 * Phase 9 Plan 09-04 — Settings → Notifications section (NOTIF-03).
 *
 * Renders the 5-kind × 3-channel toggle matrix. Each cell PATCHes
 * /api/users/me/notification-preferences with a deep partial that
 * scopes the change to the single (kind, channel) cell — preserving
 * every other cell's state.
 *
 * WhatsApp column gating (A3):
 *   • whatsapp.opted_in_at === null →
 *       — every WhatsApp cell is `disabled` with `opacity-50`,
 *       — column header renders a "verify phone" pointer link
 *         (anchor to Connectors section; the OTP verify flow lives
 *         under the Connectors panel from 09-03a).
 *   • whatsapp.opted_in_at set →
 *       — cells are interactive,
 *       — header shows a "verified" pill with the opt-in timestamp.
 *
 * Accessibility: every checkbox carries
 * `aria-label="${kind} via ${channel}"` so the testing-library role
 * queries and axe-core scan both succeed. Plan 09-04 truths block
 * asserts this contract.
 *
 * The kinds rendered in the matrix are the five user-facing ones —
 * `whatsapp_welcome` is system-driven (fires once at opt-in) and is
 * intentionally NOT shown as a user toggle.
 */
import { useMemo } from 'react';

import {
  useNotificationPreferences,
  useUpdateNotificationPreferences,
  type ChannelName,
  type NotificationKind,
  type NotificationPreferences,
} from '../../../api/notificationPreferences';
import { SettingsSection } from '../components/Field';
import { Text } from '../../../ui';
import { Skeleton } from '../../../components/ui/Skeleton';

interface KindRow {
  id: NotificationKind;
  label: string;
  hint: string;
}

const KINDS: KindRow[] = [
  {
    id: 'mention',
    label: 'Mentions',
    hint: 'Someone @-mentions you in a comment or entry body.',
  },
  {
    id: 'share',
    label: 'Shares',
    hint: 'An App, Track, or Entry is shared with you.',
  },
  {
    id: 'agent_pending_write',
    label: 'Agent pending writes',
    hint: 'An agent action requires your approval before it lands.',
  },
  {
    id: 'invitation',
    label: 'Invitations',
    hint: 'A workspace or resource invitation needs your acceptance.',
  },
  {
    id: 'system',
    label: 'System',
    hint: 'Maintenance and status updates from Integral.',
  },
];

const CHANNELS: ChannelName[] = ['in_app', 'email', 'whatsapp'];

function channelHeaderLabel(channel: ChannelName): string {
  if (channel === 'in_app') return 'In-app';
  if (channel === 'email') return 'Email';
  return 'WhatsApp';
}

function cellChecked(
  prefs: NotificationPreferences,
  kind: NotificationKind,
  channel: ChannelName,
): boolean {
  const matrix = prefs.kinds[kind] ?? prefs.channels;
  return Boolean(matrix?.[channel]);
}

export function NotificationsSection() {
  const { data: prefs, isLoading, isError, error } = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  const waOptedIn = useMemo(
    () => Boolean(prefs?.whatsapp?.opted_in_at),
    [prefs?.whatsapp?.opted_in_at],
  );

  function setCell(
    kind: NotificationKind,
    channel: ChannelName,
    value: boolean,
  ) {
    if (!prefs) return;
    const currentMatrix = prefs.kinds[kind] ?? prefs.channels;
    const nextMatrix = { ...currentMatrix, [channel]: value };
    update.mutate({
      kinds: {
        ...prefs.kinds,
        [kind]: nextMatrix,
      },
    });
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">Notifications</Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Choose which channels deliver each notification kind. WhatsApp
          requires phone verification under Connectors before it can be
          enabled.
        </Text>
      </div>

      <SettingsSection
        title="Channels"
        description="Pick which channels deliver each kind of notification."
      >
        {isLoading || !prefs ? (
          <Skeleton className="h-32 w-full" />
        ) : isError ? (
          <p className="text-sm text-[var(--danger-fg)]">
            Failed to load preferences:{' '}
            {(error as Error | undefined)?.message ?? 'unknown error'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border-subtle)]">
                  <th
                    scope="col"
                    className="py-2 pr-3 text-left text-xs font-medium uppercase tracking-wide text-[var(--text-subtle)]"
                  >
                    Kind
                  </th>
                  {CHANNELS.map(channel => (
                    <th
                      key={channel}
                      scope="col"
                      className="px-2 py-2 text-center text-xs font-medium uppercase tracking-wide text-[var(--text-subtle)]"
                    >
                      {channel === 'whatsapp' ? (
                        waOptedIn ? (
                          <span className="inline-flex items-center gap-1">
                            WhatsApp
                            <span className="rounded-[var(--radius-pill)] bg-[var(--success-bg)] px-1.5 py-0.5 text-[10px] uppercase text-[var(--success-fg)]">
                              verified
                            </span>
                          </span>
                        ) : (
                          <span className="inline-flex flex-col items-center gap-0.5">
                            <span>WhatsApp</span>
                            <a
                              href="#connectors"
                              className="text-[10px] font-normal normal-case text-[var(--brand-accent)] underline"
                            >
                              verify phone
                            </a>
                          </span>
                        )
                      ) : (
                        channelHeaderLabel(channel)
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {KINDS.map(({ id, label, hint }) => (
                  <tr
                    key={id}
                    className="border-b border-[var(--border-subtle)] last:border-b-0"
                  >
                    <td className="py-3 pr-3 align-top">
                      <div className="flex flex-col gap-0.5">
                        <span className="font-medium text-[var(--text)]">
                          {label}
                        </span>
                        <span className="text-xs text-[var(--text-subtle)]">
                          {hint}
                        </span>
                      </div>
                    </td>
                    {CHANNELS.map(channel => {
                      const disabled =
                        channel === 'whatsapp' && !waOptedIn;
                      const checked = cellChecked(prefs, id, channel);
                      return (
                        <td
                          key={channel}
                          className={`px-2 py-3 text-center align-middle ${
                            disabled ? 'opacity-50' : ''
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={disabled}
                            aria-label={`${id} via ${channel}`}
                            onChange={e => setCell(id, channel, e.target.checked)}
                            className="h-4 w-4 cursor-pointer accent-[var(--brand-accent)] disabled:cursor-not-allowed"
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SettingsSection>
    </div>
  );
}
