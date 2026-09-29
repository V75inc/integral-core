import { describe, expect, it } from 'vitest';
import { paywallForSlug, paywallLabel, type BillingCatalog, type BillingStatus } from './billingAccess';

const catalog: BillingCatalog = {
  base_configured: true,
  portal_available: true,
  addons: [
    {
      slug: 'crm',
      entitlement_key: 'crm',
      title: 'CRM',
      requires: [],
      entitled: false,
      price_configured: true,
    },
    {
      slug: 'sales',
      entitlement_key: 'sales',
      title: 'Sales',
      requires: [],
      entitled: false,
      price_configured: true,
    },
    {
      slug: 'guyana-payroll',
      entitlement_key: 'guyana-payroll',
      title: 'Guyana Payroll',
      requires: [],
      entitled: false,
      price_configured: true,
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

  it('blocks paid Apps until entitled', () => {
    expect(paywallForSlug('crm', open, catalog)).toMatchObject({
      blocked: true,
      reason: 'addon',
    });
    const decision = paywallForSlug('sales', open, catalog);
    expect(decision).toMatchObject({ blocked: true, reason: 'addon' });
    expect(paywallLabel(decision, 'Sales')).toBe('Paid add-on — unlock Sales');
  });

  it('allows an entitled add-on', () => {
    const entitled: BillingCatalog = {
      ...catalog,
      addons: catalog.addons.map(row =>
        row.slug === 'sales' ? { ...row, entitled: true } : row,
      ),
    };
    expect(paywallForSlug('sales', open, entitled).blocked).toBe(false);
  });
});
