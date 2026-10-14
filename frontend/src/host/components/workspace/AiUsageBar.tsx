import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, RefreshCw, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';

import { billingApi } from '../../api/billing';
import { LINE_ICON_STROKE } from '../../../components/ui/IconWell';

interface AiUsageBarProps {
  workspaceId?: string;
  usage?: Awaited<ReturnType<typeof billingApi.getAiUsage>>;
  refreshKey?: number | string;
  className?: string;
  /** When true, show an upgrade link on soft-warn / exhausted. */
  showUpgradeLink?: boolean;
}

function formatCredits(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0';
  return Math.round(n).toLocaleString();
}

function RefreshButton({
  busy,
  onClick,
  disabled,
}: {
  busy: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      aria-label="Refresh AI usage"
      title="Refresh"
      data-testid="ai-usage-refresh"
      className="
        inline-flex shrink-0 items-center justify-center rounded-[var(--radius-input)]
        p-1 text-[var(--text-muted)] transition-colors
        hover:bg-[var(--panel-hover)] hover:text-[var(--text)]
        disabled:pointer-events-none disabled:opacity-50
        focus-visible:outline-none focus-visible:ring-2
        focus-visible:ring-[var(--focus-ring-color)]
      "
    >
      <RefreshCw
        size={12}
        strokeWidth={LINE_ICON_STROKE}
        className={busy ? 'animate-spin' : undefined}
      />
    </button>
  );
}

export function AiUsageBar({
  workspaceId,
  usage: usageProp,
  refreshKey,
  className = '',
  showUpgradeLink = true,
}: AiUsageBarProps) {
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof billingApi.getAiUsage>> | null>(usageProp ?? null);
  const [loading, setLoading] = useState<boolean>(
    usageProp == null && Boolean(workspaceId),
  );
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const usageRef = useRef(usage);
  usageRef.current = usage;

  const fetchUsage = useCallback(async () => {
    if (usageProp || !workspaceId) return;
    setRefreshing(true);
    try {
      const u = await billingApi.getAiUsage(workspaceId);
      setUsage(u);
      setError(null);
    } catch (e: unknown) {
      if (!usageRef.current) {
        setError(e instanceof Error ? e.message : 'Could not load AI usage');
      }
    } finally {
      setRefreshing(false);
    }
  }, [workspaceId, usageProp]);

  useEffect(() => {
    if (usageProp) {
      setUsage(usageProp);
      setLoading(false);
      setError(null);
      return;
    }
    if (!workspaceId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    billingApi
      .getAiUsage(workspaceId)
      .then(u => {
        if (!cancelled) {
          setUsage(u);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setUsage(null);
          setLoading(false);
          setError(
            e instanceof Error ? e.message : 'Could not load AI usage',
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, usageProp, refreshKey]);

  const canRefresh = Boolean(workspaceId) && !usageProp;

  if (loading) {
    return (
      <div
        className={`inline-flex items-center gap-1.5 text-xs text-[var(--text-muted)] ${className}`}
      >
        <Loader2 size={12} className="animate-spin" strokeWidth={LINE_ICON_STROKE} />
        Loading AI usage…
      </div>
    );
  }
  if (error && !usage) {
    return (
      <div
        className={`flex items-center justify-between gap-2 text-xs text-[var(--danger-fg)] ${className}`}
      >
        <span className="inline-flex items-center gap-1.5">
          <AlertTriangle size={12} strokeWidth={LINE_ICON_STROKE} />
          {error}
        </span>
        {canRefresh ? (
          <RefreshButton busy={refreshing} onClick={() => void fetchUsage()} />
        ) : null}
      </div>
    );
  }
  if (!usage) return null;

  if (usage.is_unlimited) {
    return (
      <div
        className={`flex items-center justify-between gap-2 text-xs text-[var(--text-muted)] tabular-nums ${className}`}
        data-testid="ai-usage-bar"
      >
        <span className="inline-flex items-center gap-1.5">
          <Sparkles size={12} strokeWidth={LINE_ICON_STROKE} />
          {formatCredits(usage.used + (usage.exempt_used ?? 0))} credits used (unlimited)
        </span>
        {canRefresh ? (
          <RefreshButton busy={refreshing} onClick={() => void fetchUsage()} />
        ) : null}
      </div>
    );
  }

  const pct = Math.max(0, Math.min(100, usage.percent || 0));
  const weeklyExhausted = Boolean(
    usage.weekly_exhausted ?? (pct >= 100 && usage.enforcement_enabled),
  );
  const purchasedRemaining = Math.max(0, Number(usage.purchased_remaining || 0));
  const usingPurchased = Boolean(
    usage.is_using_purchased ?? (weeklyExhausted && purchasedRemaining > 0),
  );
  const fullyExhausted = Boolean(
    usage.is_exhausted && !usingPurchased,
  );
  const barClass = fullyExhausted
    ? 'bg-[var(--danger-fg)]'
    : weeklyExhausted || usage.is_soft_warning
      ? 'bg-[var(--warn-fg)]'
      : 'bg-[var(--cta-bg)]';

  const windowLabel = `${usage.window_days || 7}-day`;
  const summary = `${formatCredits(usage.used)} of ${formatCredits(usage.limit)} credits · ${pct.toFixed(0)}% · rolling ${windowLabel}`;

  return (
    <div className={`space-y-1.5 ${className}`} data-testid="ai-usage-bar">
      <div className="flex items-center justify-between gap-2 text-xs text-[var(--text-muted)] tabular-nums">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-[var(--text)]">
          <Sparkles size={12} className="shrink-0" strokeWidth={LINE_ICON_STROKE} />
          AI credits
        </span>
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <span className="truncate text-right">{summary}</span>
          {canRefresh ? (
            <RefreshButton busy={refreshing} onClick={() => void fetchUsage()} />
          ) : null}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--panel-border)]">
        <div
          className={`h-full transition-[width] duration-fast ${barClass}`}
          style={{ width: `${pct}%` }}
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          role="progressbar"
          aria-label="Workspace AI credit usage"
        />
      </div>
      {purchasedRemaining > 0 ? (
        <p
          className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--text)] tabular-nums"
          data-testid="ai-usage-purchased"
        >
          Balance: {formatCredits(purchasedRemaining)} credits
        </p>
      ) : null}
      {usingPurchased && (
        <p
          className="text-[11px] text-[var(--warn-fg)]"
          data-testid="ai-usage-using-purchased"
        >
          Weekly allowance used — spending purchased credits (
          {formatCredits(purchasedRemaining)} left)
          {showUpgradeLink ? (
            <>
              {' '}
              —{' '}
              <Link
                to="/settings#billing-credit-packs"
                className="underline underline-offset-2"
              >
                top up
              </Link>
            </>
          ) : null}
          .
        </p>
      )}
      {fullyExhausted && (
        <p className="inline-flex flex-wrap items-center gap-1 text-[11px] text-[var(--danger-fg)]">
          <AlertTriangle size={11} strokeWidth={LINE_ICON_STROKE} />
          Out of AI credits. Platform-key chat is paused
          {showUpgradeLink ? (
            <>
              {' '}
              —{' '}
              <Link
                to="/settings#billing-credit-packs"
                className="underline underline-offset-2"
              >
                top up your balance
              </Link>
              {' '}
              to continue
            </>
          ) : null}
          .
        </p>
      )}
      {!fullyExhausted && !usingPurchased && usage.is_soft_warning && (
        <p className="text-[11px] text-[var(--warn-fg)]">
          Approaching the rolling AI credit limit
          {showUpgradeLink ? (
            <>
              {' '}
              —{' '}
              <Link
                to="/settings#billing-credit-packs"
                className="underline underline-offset-2"
              >
                top up your balance
              </Link>
            </>
          ) : null}
          .
        </p>
      )}
    </div>
  );
}
