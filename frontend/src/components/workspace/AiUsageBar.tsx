import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';

import { workspacesApi } from '../../api/workspaces';
import type { WorkspaceAiUsage } from '../../api/workspaces';
import { LINE_ICON_STROKE } from '../ui/IconWell';

interface AiUsageBarProps {
  workspaceId?: string;
  usage?: WorkspaceAiUsage;
  refreshKey?: number | string;
  className?: string;
  /** When true, show an upgrade link on soft-warn / exhausted. */
  showUpgradeLink?: boolean;
}

function formatCredits(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0';
  return Math.round(n).toLocaleString();
}

export function AiUsageBar({
  workspaceId,
  usage: usageProp,
  refreshKey,
  className = '',
  showUpgradeLink = true,
}: AiUsageBarProps) {
  const [usage, setUsage] = useState<WorkspaceAiUsage | null>(usageProp ?? null);
  const [loading, setLoading] = useState<boolean>(
    usageProp == null && Boolean(workspaceId),
  );
  const [error, setError] = useState<string | null>(null);

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
    workspacesApi
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
  if (error) {
    return (
      <div
        className={`inline-flex items-center gap-1.5 text-xs text-[var(--danger-fg)] ${className}`}
      >
        <AlertTriangle size={12} strokeWidth={LINE_ICON_STROKE} />
        {error}
      </div>
    );
  }
  if (!usage) return null;

  if (usage.is_unlimited) {
    return (
      <div
        className={`inline-flex items-center gap-1.5 text-xs text-[var(--text-muted)] tabular-nums ${className}`}
      >
        <Sparkles size={12} strokeWidth={LINE_ICON_STROKE} />
        {formatCredits(usage.used)} credits used (unlimited)
      </div>
    );
  }

  const pct = Math.max(0, Math.min(100, usage.percent || 0));
  const isAtCap = Boolean(usage.is_exhausted || (pct >= 100 && usage.enforcement_enabled));
  const barClass = isAtCap
    ? 'bg-[var(--danger-fg)]'
    : usage.is_soft_warning
      ? 'bg-[var(--warn-fg)]'
      : 'bg-[var(--cta-bg)]';

  const windowLabel = `${usage.window_days || 7}-day`;
  const summary = `${formatCredits(usage.used)} of ${formatCredits(usage.limit)} credits · ${pct.toFixed(0)}% · rolling ${windowLabel}`;

  return (
    <div className={`space-y-1.5 ${className}`} data-testid="ai-usage-bar">
      <div className="flex items-center justify-between text-xs text-[var(--text-muted)] tabular-nums">
        <span className="inline-flex items-center gap-1.5 text-[var(--text)]">
          <Sparkles size={12} strokeWidth={LINE_ICON_STROKE} />
          AI credits
        </span>
        <span>{summary}</span>
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
      {isAtCap && (
        <p className="inline-flex flex-wrap items-center gap-1 text-[11px] text-[var(--danger-fg)]">
          <AlertTriangle size={11} strokeWidth={LINE_ICON_STROKE} />
          Allowance reached for this rolling window. Platform-key chat is
          paused until usage rolls off
          {showUpgradeLink ? (
            <>
              {' '}
              or you{' '}
              <Link
                to="/settings#billing"
                className="underline underline-offset-2"
              >
                upgrade your plan
              </Link>
            </>
          ) : null}
          .
        </p>
      )}
      {!isAtCap && usage.is_soft_warning && (
        <p className="text-[11px] text-[var(--warn-fg)]">
          Approaching the rolling AI credit limit
          {showUpgradeLink ? (
            <>
              {' '}
              —{' '}
              <Link
                to="/settings#billing"
                className="underline underline-offset-2"
              >
                upgrade for more
              </Link>
            </>
          ) : null}
          .
        </p>
      )}
    </div>
  );
}
