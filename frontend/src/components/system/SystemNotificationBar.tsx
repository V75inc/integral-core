/**
 * SystemNotificationBar — top-of-page banner that renders the highest-priority
 * active notification from SystemNotificationsContext.
 *
 * Visual + motion (HelloBar pattern):
 *   - position: fixed top:0, z-1000 (above app chrome, below modals at 1100)
 *   - Slide-down entrance via transform/opacity (CSS class, data-open toggle)
 *     so React re-renders don't reset the transition mid-flight
 *   - Solid dark chrome in both themes so scrolled content never bleeds through
 *   - Centered message with absolute-right dismiss
 *
 * Sidebar offset / content push-down:
 *   - Sets --system-bar-h on :root after the entrance starts; the layout
 *     root has transition: padding-top on var change → smooth content push
 *
 * Accessibility:
 *   - role="status" + aria-live (assertive for errors)
 *   - prefers-reduced-motion respected (transitions disabled via @media)
 */

import { useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle,
  Info,
  X,
  type LucideIcon,
} from 'lucide-react';
import {
  useSystemNotificationCurrent,
  useSystemNotifications,
  type SystemNotification,
  type SystemNotificationType,
} from './SystemNotificationsContext';
import { LINE_ICON_STROKE } from '../ui/IconWell';

const CSS_VAR = '--system-bar-h';
const ENTER_DELAY = 500; // ms after mount before slide-down begins

const TYPE_ICON: Record<SystemNotificationType, LucideIcon> = {
  info: Info,
  warning: AlertTriangle,
  error: AlertCircle,
  success: CheckCircle,
};

interface TypeStyle {
  accentFg: string;
}

const TYPE_STYLE: Record<SystemNotificationType, TypeStyle> = {
  info: { accentFg: 'var(--system-bar-info-fg)' },
  warning: { accentFg: 'var(--system-bar-warn-fg)' },
  error: { accentFg: 'var(--system-bar-error-fg)' },
  success: { accentFg: 'var(--system-bar-success-fg)' },
};

export function SystemNotificationBar() {
  const current = useSystemNotificationCurrent();
  const { dismiss } = useSystemNotifications();

  // open=true → slide in; flipped 500ms after a notification appears.
  const [open, setOpen] = useState(false);
  const [busyActions, setBusyActions] = useState<Record<number, boolean>>({});
  const innerRef = useRef<HTMLDivElement>(null);
  const currentId = current?.id ?? null;

  // Schedule open=true after the delay, once we have a notification.
  // While open, an ObserverObserver keeps --system-bar-h in sync with
  // the bar's natural height — so title/body/action edits after the
  // initial measurement don't leave the layout's padding-top stale.
  useEffect(() => {
    if (!currentId) {
      setOpen(false);
      document.documentElement.style.setProperty(CSS_VAR, '0px');
      return;
    }
    let ro: ResizeObserver | null = null;
    const publishHeight = () => {
      const h = innerRef.current?.getBoundingClientRect().height ?? 0;
      if (h > 0) {
        document.documentElement.style.setProperty(CSS_VAR, `${h}px`);
      }
    };
    const t = window.setTimeout(() => {
      setOpen(true);
      publishHeight();
      if (innerRef.current && typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(() => publishHeight());
        ro.observe(innerRef.current);
      }
    }, ENTER_DELAY);
    return () => {
      clearTimeout(t);
      if (ro) ro.disconnect();
      document.documentElement.style.setProperty(CSS_VAR, '0px');
    };
  }, [currentId]);

  // Reset busy state when notification swaps.
  useEffect(() => {
    setBusyActions({});
  }, [currentId]);

  if (!current) return null;

  const Icon = current.icon ?? TYPE_ICON[current.type];
  const style = TYPE_STYLE[current.type];
  const isError = current.type === 'error';
  const dismissible = current.dismissible !== false;

  const linkCls = `
    cursor-pointer font-medium whitespace-nowrap px-3 py-1.5
    hover:opacity-80 transition-opacity
    disabled:opacity-50 disabled:cursor-not-allowed
    rounded-[var(--radius-input)] hover:bg-[var(--system-bar-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--system-bar-focus)]
  `;

  const runAction = async (
    idx: number,
    action: NonNullable<SystemNotification['actions']>[number],
  ) => {
    setBusyActions((b) => ({ ...b, [idx]: true }));
    try {
      await action.onClick();
    } finally {
      setBusyActions((b) => {
        const next = { ...b };
        delete next[idx];
        return next;
      });
    }
  };

  return (
    <div
      role="status"
      aria-live={isError ? 'assertive' : 'polite'}
      data-open={open ? 'true' : 'false'}
      className="
        system-bar-animated
        fixed top-0 left-0 right-0 z-system-bar
        border-b
      "
      style={{
        backgroundColor: 'var(--system-bar-bg)',
        color: 'var(--system-bar-fg)',
        borderColor: 'var(--system-bar-border)',
      }}
    >
      <div ref={innerRef} className="relative w-full py-2 pl-4 pr-12 sm:pl-6 sm:pr-14">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-start gap-x-4 gap-y-2 sm:justify-center">
          <div className="flex min-w-0 items-start gap-2.5 sm:items-center">
            <Icon size={18} strokeWidth={LINE_ICON_STROKE} className="mt-0.5 shrink-0 sm:mt-0" style={{ color: style.accentFg }} aria-hidden />
            <p className="min-w-0 text-sm leading-snug">
              <span className="font-medium">{current.title}</span>
              {current.body && <span className="mt-0.5 block sm:ml-3 sm:mt-0 sm:inline" style={{ color: 'var(--system-bar-muted)' }}>{' '}{current.body}</span>}
            </p>
          </div>
          {current.actions && current.actions.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 pl-7 sm:pl-0">
              {current.actions.map((action, i) => {
                const busy = action.busy || !!busyActions[i];
                return (
                  <button key={i} type="button" disabled={busy} onClick={() => runAction(i, action)}
                    className={`${linkCls} ${i === 0 ? 'border' : 'border border-transparent'}`}
                    style={{ color: style.accentFg, borderColor: i === 0 ? 'var(--system-bar-border)' : undefined }}>
                    {busy ? 'Working…' : action.label}
                  </button>
                );
              })}
            </div>
          )}
        </div>
        {dismissible && (
          <button
            type="button"
            onClick={() => { current.onUserDismiss?.(); dismiss(current.id); }}
            aria-label="Dismiss notification"
            className="
              absolute right-2 top-1/2 -translate-y-1/2
              flex h-8 w-8 items-center justify-center
              rounded-[var(--radius-input)]
              text-[var(--system-bar-muted)]
              hover:bg-[var(--system-bar-hover)] hover:text-[var(--system-bar-fg)] transition-colors
              focus:outline-none focus:ring-2 focus:ring-[var(--system-bar-focus)]
            "
          >
            <X size={16} strokeWidth={LINE_ICON_STROKE} />
          </button>
        )}
      </div>
    </div>
  );
}
