import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it } from 'vitest';

import type { ManagedChannel } from '../../../../api/managedChannels';
import { ManagedChannelFullPageShare, type ManagedChannelFullPageShareRead } from '../ManagedChannelFullPageShare';

const scope = { principalId: 'manager', workspaceId: 'workspace' };
const definition = {
  slug: 'third_party_fixture', provider: 'Fixture', display_name: 'Fixture',
  description: 'Synthetic text connector.', setup_kind: 'managed-text-v1', version: 1,
} as const;
const active = {
  definition, connected: true, state: 'active', action: 'configure',
  can_enable: false, provision_id: 'provision-id', route_epoch: 'epoch',
} satisfies ManagedChannel;
const url = 'https://qualified-visitor.example.invalid/chat/opaque-fixture';
const read = {
  state: 'verified',
  owner: { ...scope, slug: definition.slug, provision_id: 'provision-id', route_epoch: 'epoch' },
  fullPageUrl: url,
} satisfies ManagedChannelFullPageShareRead;

function share(overrides: Partial<Parameters<typeof ManagedChannelFullPageShare>[0]> = {}) {
  return <ManagedChannelFullPageShare scope={scope} channel={active} read={read} canInteract {...overrides} />;
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
      share({ canInteract: false }),
    ]) {
      rerender(share());
      expect(screen.getByRole('link')).toBeVisible();
      rerender(variant);
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
      expect(screen.queryByText(url)).not.toBeInTheDocument();
    }
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
    render(share({ read: { ...read, fullPageUrl: bad } }));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByText('Full-page sharing')).not.toBeInTheDocument();
  });
});
