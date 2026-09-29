import { Badge } from './Badge';

export type PlanBadgeInput = {
  plan_key?: string | null;
  plan_label?: string | null;
  subscription_status?: string | null;
  cancel_at_period_end?: boolean | null;
};

/** Normalize legacy `base` → `basic`; empty → `free`. */
export function normalizePlanKey(planKey?: string | null): string {
  const key = (planKey || '').trim().toLowerCase();
  if (!key || key === 'free') return 'free';
  if (key === 'base') return 'basic';
  return key;
}

export function planLabelForKey(planKey?: string | null): string {
  const key = normalizePlanKey(planKey);
  if (key === 'free') return 'Free';
  if (key === 'basic') return 'Basic';
  if (key === 'premium') return 'Premium';
  return key.replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function badgeVariant(planKey: string): string {
  if (planKey === 'premium') return 'info';
  if (planKey === 'basic') return 'primary';
  return 'default';
}

function statusTitle(
  status?: string | null,
  cancelAtPeriodEnd?: boolean | null,
): string | undefined {
  if (cancelAtPeriodEnd) return 'Canceling at period end';
  const raw = (status || '').trim().toLowerCase();
  if (raw === 'past_due') return 'Past due';
  if (raw === 'trialing') return 'Free trial';
  return undefined;
}

/**
 * Compact plan chip for workspace list / detail / switcher.
 * Missing or unpaid → Free. Optional title for canceling / past due.
 */
export function PlanBadge({
  plan_key,
  plan_label,
  subscription_status,
  cancel_at_period_end,
  className = '',
  compact = false,
}: PlanBadgeInput & { className?: string; compact?: boolean }) {
  const key = normalizePlanKey(plan_key);
  const label = (plan_label || '').trim() || planLabelForKey(key);
  const title = statusTitle(subscription_status, cancel_at_period_end);
  return (
    <Badge
      variant={badgeVariant(key)}
      className={`${compact ? 'px-1.5 py-0 text-[10px] leading-4' : ''} ${className}`.trim()}
    >
      <span title={title}>{label}</span>
    </Badge>
  );
}
