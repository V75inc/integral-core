import { describe, expect, it } from 'vitest';
import { paywallForSlug, paywallLabel, type BillingCatalog, type BillingStatus } from './billingAccess';

const catalog: BillingCatalog = {
  base_configured: true,
  portal_available: true,
  addons: [
    {
      slug: 'documents',
      entitlement_key: 'documents',
      title: 'Documents',
      requires: [],
      entitled: false,
      price_configured: true,
    },
    {
      slug: 'sales',
      entitlement_key: 'sales',
      title: 'Sales',
      requires: ['documents'],
      entitled: false,
      price_configured: true,
    },
  ],
};

const open: BillingStatus = { subscription_required: true, access: 'open' };
const locked: BillingStatus = { subscription_required: true, access: 'locked' };

describe('paywallForSlug', () => {
  it('leaves open-source installs alone', () => {
    const decision = paywallForSlug('documents', { subscription_required: false, access: 'off' }, catalog);
    expect(decision.blocked).toBe(false);
  });

  it('still allows free Apps when billing is on but unpaid', () => {
    expect(paywallForSlug('crm', locked, catalog).blocked).toBe(false);
    expect(paywallForSlug('org_app', locked, catalog).blocked).toBe(false);
  });

  it('blocks a paid App until it is entitled', () => {
    const decision = paywallForSlug('documents', open, catalog);
    expect(decision).toMatchObject({ blocked: true, reason: 'addon' });
    expect(paywallLabel(decision, 'Documents')).toBe('Paid add-on — unlock Documents');
  });

  it('refuses Sales until Documents is entitled', () => {
    const decision = paywallForSlug('sales', open, catalog);
    expect(decision.reason).toBe('dependency');
    expect(decision.missing).toEqual(['documents']);
  });

  it('allows an entitled add-on', () => {
    const entitled: BillingCatalog = {
      ...catalog,
      addons: catalog.addons.map(row =>
        row.slug === 'documents' ? { ...row, entitled: true } : row,
      ),
    };
    expect(paywallForSlug('documents', open, entitled).blocked).toBe(false);
  });
});
