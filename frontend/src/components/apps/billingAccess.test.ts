import { describe, expect, it } from 'vitest';
import {
  paywallForSlug,
  paywallLabel,
  type BillingCatalog,
  type BillingStatus,
} from './billingAccess';

const catalog: BillingCatalog = {
  any_plan_configured: true,
  portal_available: true,
  has_subscription: false,
  current_plan_key: null,
  plans: [
    {
      key: 'basic',
      title: 'Basic',
      description: 'CRM and payroll',
      rank: 10,
      price_configured: true,
      apps: ['crm', 'guyana-payroll'],
    },
    {
      key: 'premium',
      title: 'Premium',
      description: 'Everything in Basic, plus Sales',
      rank: 20,
      price_configured: true,
      apps: ['crm', 'guyana-payroll', 'sales'],
    },
  ],
  apps: [
    {
      slug: 'crm',
      entitlement_key: 'crm',
      title: 'CRM',
      min_plan: 'basic',
      entitled: false,
    },
    {
      slug: 'sales',
      entitlement_key: 'sales',
      title: 'Sales',
      min_plan: 'premium',
      entitled: false,
    },
    {
      slug: 'guyana-payroll',
      entitlement_key: 'guyana-payroll',
      title: 'Guyana Payroll',
      min_plan: 'basic',
      entitled: false,
    },
  ],
};

const open: BillingStatus = { subscription_required: true, access: 'open' };
const locked: BillingStatus = { subscription_required: true, access: 'locked' };

describe('paywallForSlug', () => {
  it('leaves open-source installs alone', () => {
    const decision = paywallForSlug(
      'sales',
      { subscription_required: false, access: 'off' },
      catalog,
    );
    expect(decision.blocked).toBe(false);
  });

  it('allows free Apps even when billing is on', () => {
    expect(paywallForSlug('documents', locked, catalog).blocked).toBe(false);
    expect(paywallForSlug('org_app', locked, catalog).blocked).toBe(false);
  });

  it('blocks commercial Apps until entitled and names the plan', () => {
    expect(paywallForSlug('crm', open, catalog)).toMatchObject({
      blocked: true,
      reason: 'plan',
      min_plan: 'basic',
    });
    const decision = paywallForSlug('sales', open, catalog);
    expect(decision).toMatchObject({
      blocked: true,
      reason: 'plan',
      min_plan: 'premium',
    });
    expect(paywallLabel(decision, 'Sales')).toBe('Included in Premium');
  });

  it('allows an entitled App', () => {
    const entitled: BillingCatalog = {
      ...catalog,
      apps: catalog.apps.map(row =>
        row.slug === 'sales' ? { ...row, entitled: true } : row,
      ),
    };
    expect(paywallForSlug('sales', open, entitled).blocked).toBe(false);
  });
});
