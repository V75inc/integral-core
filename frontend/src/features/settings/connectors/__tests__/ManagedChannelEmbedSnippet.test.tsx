import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ManagedChannel } from '../../../../api/managedChannels';
import { ManagedChannelEmbedSnippet } from '../ManagedChannelEmbedSnippet';
import {
  currentManagedEmbedSnippet,
  decodeApprovedManagedEmbed,
  type ManagedChannelEmbedRead,
  type ManagedEmbedOwner,
} from '../managedChannelEmbedProof';

const scope = { principalId: 'private-manager', workspaceId: 'private-workspace', sessionFence: 'local-fence' };
const publication = { profileKey: 'private-profile', publishedVersion: 14 };
const policy = { parentOrigin: 'https://approved-site.example.invalid', policyRevision: 3 };
const channel = {
  definition: {
    slug: 'synthetic_text', provider: 'Test', display_name: 'Fixture',
    description: 'No live route', setup_kind: 'managed-text-v1', version: 1,
  },
  connected: true, state: 'active', action: 'configure', can_enable: false,
  provision_id: 'private-provision', route_epoch: 'private-epoch',
} satisfies ManagedChannel;
const owner: ManagedEmbedOwner = {
  ...scope, slug: channel.definition.slug, provisionId: channel.provision_id,
  routeEpoch: channel.route_epoch, publication, policy,
};
const embed = 'https://embed-host.example.invalid/embed/unserved_opaque';
const raw = {
  kind: 'approved-web-chat-embed-v1', workspace_id: scope.workspaceId, slug: owner.slug,
  provision_id: owner.provisionId, route_epoch: owner.routeEpoch, profile_key: publication.profileKey,
  published_version: publication.publishedVersion, embed_policy_revision: policy.policyRevision,
  parent_origin: policy.parentOrigin, embed_page_url: embed,
};
const proof = decodeApprovedManagedEmbed(raw, owner);

function panel(props: Partial<Parameters<typeof ManagedChannelEmbedSnippet>[0]> = {}) {
  return <ManagedChannelEmbedSnippet scope={scope} channel={channel} publication={publication}
    policy={policy} read={proof} canInteract {...props} />;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function clipboard(writeText: (value: string) => Promise<void>) {
  const initial = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  return () => {
    if (initial) Object.defineProperty(navigator, 'clipboard', initial);
    else Reflect.deleteProperty(navigator, 'clipboard');
  };
}

describe('detached embed proof', () => {
  it('renders literal JavaScript, never a live script/iframe and never private identity values', () => {
    const { container } = render(panel());
    const field = screen.getByRole('textbox', { name: 'Embed code' });
    expect(field).toHaveAttribute('readonly');
    const snippet = currentManagedEmbedSnippet(proof, owner);
    expect(field).toHaveValue(snippet);
    expect(snippet).toContain(embed);
    expect(snippet).toContain(policy.parentOrigin);
    expect(snippet).not.toMatch(/private-|local-fence|\/chat\/|postMessage|document\.cookie|innerHTML|allow-forms|allow-popups|allow-top-navigation/);
    expect(container.querySelector('script, iframe, a')).toBeNull();
  });

  it('executes only at the approved exact parent and creates a restricted cross-origin iframe when pasted', () => {
    const snippet = currentManagedEmbedSnippet(proof, owner);
    const code = snippet?.match(/<script>([\s\S]*)<\/script>/)?.[1];
    expect(code).toBeTruthy();
    // The fixture DOM is detached; there is no fetch or live service.
    const run = new Function('document', 'window', code ?? '') as (doc: unknown, win: unknown) => void;
    const mount = document.createElement('div');
    mount.setAttribute('data-integral-web-chat', '');
    const createElement = vi.fn((tag: string) => document.createElement(tag));
    const fakeDocument = { currentScript: { previousElementSibling: mount }, createElement };

    for (const origin of ['https://another-site.example.invalid', `${policy.parentOrigin}:9443`, 'null']) {
      run(fakeDocument, { location: { origin } });
    }
    expect(createElement).not.toHaveBeenCalled();
    expect(mount.childElementCount).toBe(0);
    run(fakeDocument, { location: { origin: policy.parentOrigin } });
    const iframe = mount.querySelector('iframe');
    expect(iframe?.src).toBe(embed);
    expect(iframe?.title).toBe('Integral Web Chat');
    expect(iframe?.getAttribute('loading')).toBe('lazy');
    expect(iframe?.getAttribute('sandbox')).toBe('allow-scripts allow-same-origin');
    expect(iframe?.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(iframe?.getAttribute('allow')).toBeNull();
  });

  it.each([
    { kind: 'approved-full-page-share-v1' },
    { full_page_url: 'https://embed-host.example.invalid/chat/opaque' },
    { embed_page_url: 'https://embed-host.example.invalid/chat/opaque' },
    { embed_page_url: 'http://embed-host.example.invalid/embed/opaque' },
    { embed_page_url: 'https://embed-host.example.invalid/embed/opaque?token=synthetic' },
    { embed_page_url: 'https://embed-host.example.invalid/embed/opaque#synthetic' },
    { embed_page_url: 'https://user:pass@embed-host.example.invalid/embed/opaque' },
    { embed_page_url: 'https://embed-host.example.invalid/embed/a%2Fb' },
    { embed_page_url: 'https://embed-host.example.invalid/embed/../embed/opaque' },
    { embed_page_url: 'https://embed-host.example.invalid/embed/opaque/extra' },
    { embed_page_url: 'https://localhost/embed/opaque' },
    { embed_page_url: 'https://127.0.0.1/embed/opaque' },
    { embed_page_url: `${policy.parentOrigin}/embed/disallowed-same-host` },
    { embed_page_url: 'https://embed-host.example.invalid/embed/opaque</script>' },
    { parent_origin: `${policy.parentOrigin}/` },
    { parent_origin: `${policy.parentOrigin}/path` },
    { parent_origin: 'https://APPROVED-SITE.example.invalid' },
    { parent_origin: 'https://approved-site.example.invalid:443' },
    { parent_origin: 'https://approved-site.example.invalid.' },
    { parent_origin: 'https://local.localhost' },
    { embed_policy_revision: 0 },
    { embed_policy_revision: 4 },
    { profile_key: 'another-profile' },
    { published_version: 13 },
    { visitor_token: 'not allowed' },
    { route_epoch: 'obsolete' },
    { provision_id: 'wrong-provision' },
    { workspace_id: 'other-workspace' },
  ])('rejects a foreign, extra, malformed, or stale proof (%j)', change => {
    const request = typeof change.parent_origin === 'string'
      ? { ...owner, policy: { ...policy, parentOrigin: change.parent_origin } } : owner;
    const invalid = decodeApprovedManagedEmbed({ ...raw, ...change }, request);
    expect(invalid).toEqual({ state: 'unavailable' });
  });

  it('never retains a prior proof across active state, actor/session, publication or embed policy changes', () => {
    const forged = { ...proof, snippet: 'unvalidated' } as ManagedChannelEmbedRead;
    const { rerender } = render(panel());
    for (const next of [
      panel({ channel: null }),
      panel({ channel: { ...channel, state: 'inactive' } }),
      panel({ channel: { ...channel, state: 'unavailable' } }),
      panel({ channel: { ...channel, route_epoch: 'changed' } }),
      panel({ scope: { ...scope, principalId: 'another' } }),
      panel({ scope: { ...scope, sessionFence: 'another-login' } }),
      panel({ scope: { ...scope, workspaceId: 'different' } }),
      panel({ publication: null }),
      panel({ publication: { ...publication, publishedVersion: 15 } }),
      panel({ policy: null }),
      panel({ policy: { ...policy, policyRevision: 4 } }),
      panel({ policy: { ...policy, parentOrigin: 'https://other-site.example.invalid' } }),
      panel({ read: { state: 'checking' } }),
      panel({ read: forged }),
      panel({ canInteract: false }),
    ]) {
      rerender(panel());
      expect(screen.getByRole('button', { name: 'Copy embed code' })).toBeVisible();
      rerender(next);
      expect(screen.queryByRole('button', { name: 'Copy embed code' })).not.toBeInTheDocument();
    }
  });

  it('copies only on explicit action and reports no stale completion after a policy change', async () => {
    let finish!: () => void;
    const write = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const restore = clipboard(write);
    try {
      const { rerender } = render(panel());
      const button = screen.getByRole('button', { name: 'Copy embed code' });
      expect(write).not.toHaveBeenCalled();
      fireEvent.click(button);
      fireEvent.click(button);
      expect(write).toHaveBeenCalledExactlyOnceWith(currentManagedEmbedSnippet(proof, owner));
      rerender(panel({ policy: { ...policy, policyRevision: 4 } }));
      await act(async () => finish());
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
    } finally {
      restore();
    }
  });

  it('announces success and leaves a manual-select fallback for clipboard failure', async () => {
    const write = vi.fn<(value: string) => Promise<void>>().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('blocked'));
    const restore = clipboard(write);
    try {
      render(panel());
      const button = screen.getByRole('button', { name: 'Copy embed code' });
      fireEvent.click(button);
      expect(await screen.findByText('Embed code copied.')).toBeVisible();
      fireEvent.click(button);
      expect(await screen.findByText(/Select and copy the code above/)).toBeVisible();
      expect(screen.getByRole('textbox', { name: 'Embed code' })).toHaveValue(currentManagedEmbedSnippet(proof, owner));
    } finally {
      restore();
    }
  });
});
