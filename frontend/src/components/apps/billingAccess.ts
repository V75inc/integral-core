/**
 * Which Manage Apps rows a commercial plan paywall blocks.
 * Free Apps (not in the billing catalog: Documents, Organization) always
 * install. The install API stays the gate for commercial_app packages.
 */

export interface BillingApp {
  slug: string;
  entitlement_key: string;
  title?: string;
  description?: string;
  min_plan: string;
  entitled: boolean;
}

export interface BillingPlan {
  key: string;
  title: string;
  description?: string;
  rank: number;
  price_configured: boolean;
  apps: string[];
}

export interface BillingStatus {
  subscription_required: boolean;
  access: 'off' | 'open' | 'grace' | 'locked' | string;
  workspace_id?: string;
  status?: string | null;
  plan_key?: string | null;
  billing_account_id?: string | null;
  grace_until?: string | null;
  source?: string | null;
  checkout_available?: boolean;
}

export interface BillingCatalog {
  any_plan_configured: boolean;
  trial_days?: number;
  grace_days?: number;
  portal_available: boolean;
  has_subscription?: boolean;
  current_plan_key?: string | null;
  plans: BillingPlan[];
  apps: BillingApp[];
}

export interface HostedSubscription {
  workspace_id: string;
  workspace_name?: string;
  billing_account_id: string;
  status: string;
  plan_key: string;
  source: string;
  external_customer_id: string;
  external_subscription_id: string;
  past_due_since?: string | null;
  access_until?: string | null;
  access: string;
  grace_until?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface HostedSubscriptionList {
  subscriptions: HostedSubscription[];
  total: number;
}

export interface HostedSubscriptionUpsert {
  workspace_id: string;
  status: string;
  billing_account_id?: string;
  plan_key?: string;
  external_customer_id?: string;
  external_subscription_id?: string;
  past_due_since?: string | null;
  access_until?: string | null;
}

export type PaywallReason = 'plan' | null;

export interface PaywallDecision {
  blocked: boolean;
  reason: PaywallReason;
  min_plan: string | null;
  plan_title: string | null;
}

export function planByKey(
  catalog: BillingCatalog | null,
  key: string | null | undefined,
): BillingPlan | null {
  const want = (key || '').trim().toLowerCase();
  if (!want || !catalog?.plans?.length) return null;
  return catalog.plans.find(row => row.key === want) ?? null;
}

export function appBySlug(
  catalog: BillingCatalog | null,
  slug: string,
): BillingApp | null {
  const key = (slug || '').trim().toLowerCase();
  if (!key || !catalog?.apps?.length) return null;
  return catalog.apps.find(row => row.slug === key) ?? null;
}

export function paywallForSlug(
  slug: string,
  status: BillingStatus | null,
  catalog: BillingCatalog | null,
): PaywallDecision {
  // Free / community Apps are not in the catalog — never block them.
  if (!status?.subscription_required || status.access === 'off') {
    return { blocked: false, reason: null, min_plan: null, plan_title: null };
  }
  const app = appBySlug(catalog, slug);
  if (!app) {
    return { blocked: false, reason: null, min_plan: null, plan_title: null };
  }
  if (app.entitled) {
    return { blocked: false, reason: null, min_plan: null, plan_title: null };
  }
  const plan = planByKey(catalog, app.min_plan);
  return {
    blocked: true,
    reason: 'plan',
    min_plan: app.min_plan,
    plan_title: plan?.title || app.min_plan,
  };
}

export function paywallLabel(decision: PaywallDecision, name: string): string | null {
  if (!decision.blocked || !decision.plan_title) return null;
  void name;
  return `Included in ${decision.plan_title}`;
}

export function appDisplayName(app: BillingApp): string {
  return (app.title || app.slug).trim() || app.slug;
}

/** @deprecated Use appDisplayName — kept for call sites during the tier migration. */
export function addonDisplayName(app: BillingApp): string {
  return appDisplayName(app);
}
