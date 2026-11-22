import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it } from 'vitest';

import type { ManagedChannel } from '../../../../api/managedChannels';
import { ManagedChannelFullPageShare } from '../ManagedChannelFullPageShare';
import { decodeAuthorizedManagedShare, type ManagedChannelFullPageShareRead } from '../managedChannelShareProof';

const scope = { principalId: 'manager', workspaceId: 'workspace', sessionFence: 'scope-generation-one' };
const publication = { profileKey: 'public', publishedVersion: 7 };
const definition = {
  slug: 'third_party_fixture', provider: 'Fixture', display_name: 'Fixture',
  description: 'Synthetic text connector.', setup_kind: 'managed-text-v1', version: 1,
} as const;
const active = {
  definition, connected: true, state: 'active', action: 'configure',
  can_enable: false, provision_id: 'provision-id', route_epoch: 'epoch',
} satisfies ManagedChannel;
const url = 'https://qualified-visitor.example.invalid/chat/opaque-fixture';
const owner = { ...scope, slug: definition.slug, provisionId: 'provision-id', routeEpoch: 'epoch', publication };
const wire = {
  kind: 'approved-full-page-share-v1', workspace_id: scope.workspaceId, slug: definition.slug,
  provision_id: owner.provisionId, route_epoch: owner.routeEpoch,
  profile_key: publication.profileKey, published_version: publication.publishedVersion, full_page_url: url,
};
const read = decodeAuthorizedManagedShare(wire, owner);

function share(overrides: Partial<Parameters<typeof ManagedChannelFullPageShare>[0]> = {}) {
  return <ManagedChannelFullPageShare scope={scope} channel={active} publication={publication} read={read}
    canInteract {...overrides} />;
}

describe('detached managed full-page sharing', () => {
  it('displays the server projection literally for the exact current active slot', () => {
    render(share());
    expect(screen.getByText(url)).toBeVisible();
    const link = screen.getByRole('link', { name: 'Open full-page chat' });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveAttribute('referrerpolicy', 'no-referrer');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('removes visibility immediately on readiness, view, or generation change', () => {
    const { rerender } = render(share());
    for (const variant of [
      share({ read: { state: 'checking' } }),
      share({ read: { state: 'unavailable' } }),
      share({ channel: null }),
      share({ channel: { ...active, state: 'inactive' } }),
      share({ channel: { ...active, state: 'unavailable' } }),
      share({ channel: { ...active, provision_id: 'new-provision' } }),
      share({ channel: { ...active, route_epoch: 'new-epoch' } }),
      share({ channel: { ...active, definition: { ...definition, slug: 'another' } } }),
      share({ scope: { ...scope, workspaceId: 'another' } }),
      share({ scope: { ...scope, principalId: 'another' } }),
      share({ scope: { ...scope, principalId: '' } }),
      share({ scope: { ...scope, sessionFence: 'next-session-same-manager' } }),
      share({ publication: null }),
      share({ publication: { ...publication, publishedVersion: 8 } }),
      share({ publication: { ...publication, profileKey: 'another' } }),
      share({ canInteract: false }),
    ]) {
      rerender(share());
      expect(screen.getByRole('link')).toBeVisible();
      rerender(variant);
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
      expect(screen.queryByText(url)).not.toBeInTheDocument();
    }
  });

  it('rejects a replay, extra credential field, or management DTO as an authorized share read', () => {
    for (const bad of [
      { ...wire, kind: 'another-version' },
      { ...wire, workspace_id: 'another-workspace' },
      { ...wire, slug: 'misbound' },
      { ...wire, provision_id: 'retired' },
      { ...wire, route_epoch: 'stale' },
      { ...wire, profile_key: 'wrong' },
      { ...wire, published_version: 6 },
      { ...wire, published_version: '7' },
      { ...wire, token: 'must not echo credentials' },
      { ...wire, manager_id: scope.principalId, sessionFence: scope.sessionFence },
      { channels: [{ ...active, full_page_url: url }] },
      { kind: 'published-public-profile-v1', profile_revision: 7, ...active, full_page_url: url },
    ]) {
      expect(decodeAuthorizedManagedShare(bad, owner)).toEqual({ state: 'unavailable' });
    }
    expect(decodeAuthorizedManagedShare(wire, { ...owner, sessionFence: '' })).toEqual({ state: 'unavailable' });
  });

  it('does not trust a plain object that calls itself verified', () => {
    const forged = { state: 'verified', owner, fullPageUrl: url } as ManagedChannelFullPageShareRead;
    const { rerender } = render(share({ read: forged }));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    if (read.state !== 'verified') throw new Error('Expected a valid fixture projection');
    rerender(share({ read: { ...read, fullPageUrl: 'https://other.example.invalid/chat/wrong' } }));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it.each([
    null, true, {}, '/chat/abc', '//foreign.example.invalid/chat/abc',
    'http://qualified-visitor.example.invalid/chat/a',
    'javascript:alert(1)',
    'https://user:pass@qualified-visitor.example.invalid/chat/a',
    'https://qualified-visitor.example.invalid/chat/a?v=token',
    'https://qualified-visitor.example.invalid/chat/a#token',
    'https://qualified-visitor.example.invalid/chat/a?',
    'https://qualified-visitor.example.invalid/chat/a#',
    'https://qualified-visitor.example.invalid/chat/a\n',
    'https://qualified-visitor.example.invalid\\chat\\a',
    'https://QUALIFIED-VISITOR.example.invalid/chat/a',
    'https://qualified-visitor.example.invalid:443/chat/a',
    'https://qualified-visitor.example.invalid/chat/../another',
    'https://qualified-visitor.example.invalid:0/chat/a',
    'https://localhost/chat/a',
    'https://127.0.0.1/chat/a',
    'https://[::1]/chat/a',
  ])('does not expose malformed or noncanonical URL (%j)', (bad) => {
    render(share({ read: decodeAuthorizedManagedShare({ ...wire, full_page_url: bad }, owner) }));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByText('Full-page sharing')).not.toBeInTheDocument();
  });
});
