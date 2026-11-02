import { describe, expect, it, vi } from 'vitest';
import { resolveBusinessInstallDenial } from './installDenial';

const denial = {error_code: 'entitlement.required', details: {entitlement_key: 'private-key'}};

describe('Business install denial', () => {
  it.each([denial, {response: {data: denial}}, {response: {data: {detail: denial}}}])('maps transport and batch denials to the existing billing surface', err => {
    const navigate = vi.fn();
    const action = resolveBusinessInstallDenial(err, navigate);
    expect(action?.message).toBe('App access is required. Review this workspace’s app access before installing.');
    expect(action?.message).not.toContain('private-key');
    expect(action?.actionLabel).toBe('Review app access');
    action?.onAction?.();
    expect(navigate).toHaveBeenCalledOnce();
  });

  it.each([null, undefined, [], 'entitlement.required', {error_code: 'install_failed'}, {response: {data: {details: {entitlement_key: 'private-key'}}}}, {error_code: 'app_dependency_error'}])('leaves unrelated or malformed failures to Core', err => {
    expect(resolveBusinessInstallDenial(err, vi.fn())).toBeNull();
  });

  it('can guide an entitlement denial without assuming a plan or a technical key', () => {
    expect(resolveBusinessInstallDenial({error_code: 'entitlement.required'}, vi.fn())?.actionLabel).toBe('Review app access');
  });
});
