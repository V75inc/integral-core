/**
 * Which Manage Apps rows the subscription lock blocks.
 * The install API stays the gate. This only explains it.
 */

export interface BillingAddon {
  slug: string;
  entitlement_key: string;
  requires: string[];
  entitled: boolean;
  price_configured: boolean;
}

export interface BillingStatus {
  subscription_required: boolean;
  access: 'off' | 'open' | 'grace' | 'locked' | string;
  workspace_id?: string;
  status?: string | null;
  grace_until?: string | null;
  checkout_available?: boolean;
}

export interface BillingCatalog {
  base_configured: boolean;
  trial_days?: number;
  portal_available: boolean;
  addons: BillingAddon[];
}

export type PaywallReason = 'base' | 'addon' | 'dependency' | null;

export interface PaywallDecision {
  blocked: boolean;
  reason: PaywallReason;
  missing: string[];
}

export function paywallForSlug(
  slug: string,
  status: BillingStatus | null,
  catalog: BillingCatalog | null,
): PaywallDecision {
  if (!status?.subscription_required || status.access === 'off') {
    return { blocked: false, reason: null, missing: [] };
  }
  if (status.access === 'locked') {
    return { blocked: true, reason: 'base', missing: [] };
  }
  const key = (slug || '').trim().toLowerCase();
  const addon = catalog?.addons.find(row => row.slug === key);
  if (!addon || addon.entitled) {
    return { blocked: false, reason: null, missing: [] };
  }
  const missing = addon.requires.filter(req => {
    const dep = catalog?.addons.find(row => row.slug === req);
    return !dep?.entitled;
  });
  if (missing.length > 0) {
    return { blocked: true, reason: 'dependency', missing };
  }
  return { blocked: true, reason: 'addon', missing: [] };
}

export function paywallLabel(decision: PaywallDecision, name: string): string | null {
  if (!decision.blocked) return null;
  if (decision.reason === 'base') return 'Subscribe to install apps';
  if (decision.reason === 'dependency') {
    return `Requires ${decision.missing.join(' and ')}`;
  }
  return `Add ${name}`;
}
