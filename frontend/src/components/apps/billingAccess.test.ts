import { describe, expect, it } from 'vitest';
import { paywallForSlug, paywallLabel, type BillingCatalog, type BillingStatus } from './billingAccess';

const catalog: BillingCatalog = {
  base_configured: true,
  portal_available: true,
  addons: [
    {
      slug: 'documents',
      entitlement_key: 'documents',
      requires: [],
      entitled: false,
      price_configured: true,
    },
    {
      slug: 'crm',
      entitlement_key: 'crm',
      requires: [],
      entitled: true,
      price_configured: true,
    },
    {
      slug: 'sales',
      entitlement_key: 'sales',
      requires: ['crm', 'documents'],
      entitled: false,
      price_configured: true,
    },
  ],
};

const open: BillingStatus = { hosted: true, access: 'open' };

describe('paywallForSlug', () => {
  it('leaves open-source installs alone', () => {
    const decision = paywallForSlug('documents', { hosted: false, access: 'unhosted' }, catalog);
    expect(decision.blocked).toBe(false);
  });

  it('blocks every install when the base plan is locked', () => {
    const decision = paywallForSlug(
      'documents',
      { hosted: true, access: 'locked' },
      catalog,
    );
    expect(decision).toMatchObject({ blocked: true, reason: 'base' });
    expect(paywallLabel(decision, 'Documents')).toBe('Subscribe to install apps');
  });

  it('asks for an add-on when the base plan is active', () => {
    const decision = paywallForSlug('documents', open, catalog);
    expect(decision).toMatchObject({ blocked: true, reason: 'addon' });
    expect(paywallLabel(decision, 'Documents')).toBe('Add Documents');
  });

  it('refuses Sales until CRM and Documents are entitled', () => {
    const decision = paywallForSlug('sales', open, catalog);
    expect(decision.reason).toBe('dependency');
    expect(decision.missing).toEqual(['documents']);
  });

  it('allows a community app and an entitled add-on', () => {
    expect(paywallForSlug('org_app', open, catalog).blocked).toBe(false);
    expect(paywallForSlug('crm', open, catalog).blocked).toBe(false);
  });
});
