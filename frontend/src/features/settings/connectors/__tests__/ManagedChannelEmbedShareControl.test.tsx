import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ManagedChannel } from '../../../../api/managedChannels';
import { ManagedChannelEmbedShareControl } from '../ManagedChannelEmbedShareControl';
import {
  decodeApprovedManagedEmbed,
  type ManagedChannelEmbedRead,
  type ManagedEmbedOwner,
} from '../managedChannelEmbedProof';
import type { ManagedChannelEmbedShareReader } from '../managedChannelEmbedShareReader';

type Props = ComponentProps<typeof ManagedChannelEmbedShareControl>;

const channel = {
  definition: { slug: 'public_web_chat', provider: 'Fixture', display_name: 'Web Chat',
    description: 'Synthetic fixture', setup_kind: 'managed-text-v1', version: 1 },
  connected: true, action: 'configure', state: 'active', can_enable: false,
  provision_id: 'installed', route_epoch: '23', configuration_kind: 'published-public-profile-v1',
} satisfies ManagedChannel;
const scope = { principalId: 'manager', workspaceId: 'shared', sessionFence: '8' };
const publication = { profileKey: 'public', publishedVersion: 4 };
const policy = { parentOrigin: 'https://publisher.example.com', policyRevision: 6 };
const url = 'https://detached.example.com/embed/fixture';

function approve(owner: ManagedEmbedOwner, pageUrl = url) {
  return decodeApprovedManagedEmbed({
    kind: 'approved-web-chat-embed-v1', workspace_id: owner.workspaceId, slug: owner.slug,
    provision_id: owner.provisionId, route_epoch: owner.routeEpoch, profile_key: owner.publication.profileKey,
    published_version: owner.publication.publishedVersion, embed_policy_revision: owner.policy.policyRevision,
    parent_origin: owner.policy.parentOrigin, embed_page_url: pageUrl,
  }, owner);
}

function props(read: ManagedChannelEmbedShareReader): Props {
  return { scope, source: { scope, read }, channel, publication, policy, profileRevision: 1, canInteract: true };
}

function deferredRead() {
  const calls: Array<{ owner: ManagedEmbedOwner; signal: AbortSignal;
    release: (value: ManagedChannelEmbedRead) => void }> = [];
  const read = vi.fn<ManagedChannelEmbedShareReader>((owner, signal) => new Promise(resolve => {
    calls.push({ owner, signal, release: resolve });
  }));
  return { calls, read };
}

const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
const writeText = vi.fn<(value: string) => Promise<void>>();
const check = () => screen.getByRole('button', { name: 'Check embed code' });
const snippet = () => screen.queryByRole<HTMLTextAreaElement>('textbox', { name: 'Embed code' });

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
});

afterEach(() => {
  if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
  else Reflect.deleteProperty(navigator, 'clipboard');
});

describe('detached Web Chat embed-share gesture control', () => {
  it('reads once per explicit check, shows only a bound snippet, and copies only on click', async () => {
    const { calls, read } = deferredRead();
    render(<ManagedChannelEmbedShareControl {...props(read)} />);
    expect(read).not.toHaveBeenCalled();
    expect(snippet()).not.toBeInTheDocument();
    fireEvent.click(check());
    fireEvent.click(check());
    expect(read).toHaveBeenCalledExactlyOnceWith(
      { ...scope, slug: channel.definition.slug, provisionId: 'installed', routeEpoch: '23', publication, policy },
      expect.any(AbortSignal));
    expect(screen.getByRole('status')).toHaveTextContent('Checking embed sharing');
    await act(async () => { calls[0].release(approve(calls[0].owner)); });
    expect(snippet()?.value).toContain(url);
    expect(writeText).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Copy embed code' }));
    expect(await screen.findByText('Embed code copied.')).toBeVisible();
    expect(writeText).toHaveBeenCalledExactlyOnceWith(expect.stringContaining(url));
  });

  const ineligible: Array<[string, (p: Props) => Partial<Props>]> = [
    ['closed', () => ({ canInteract: false })],
    ['no current manager', () => ({ scope: null })],
    ['no reviewed reader', () => ({ source: undefined })],
    ['wrong manager', p => ({ source: { ...p.source!, scope: { ...scope, principalId: 'other' } } })],
    ['wrong session', p => ({ source: { ...p.source!, scope: { ...scope, sessionFence: '7' } } })],
    ['wrong workspace', p => ({ source: { ...p.source!, scope: { ...scope, workspaceId: 'elsewhere' } } })],
    ['unavailable', () => ({ channel: { ...channel, state: 'unavailable' } })],
    ['inactive', () => ({ channel: { ...channel, state: 'inactive' } })],
    ['uninstalled', () => ({ channel: { definition: channel.definition, state: 'registered',
      action: 'add', connected: false, can_install: true } })],
    ['unrelated connector', () => ({ channel: { ...channel, definition: { ...channel.definition, slug: 'another' } } })],
    ['no published config capability', () => ({ channel: { ...channel, configuration_kind: undefined } })],
    ['unpublished', () => ({ publication: null })],
    ['no policy', () => ({ policy: null })],
    ['invalid origin', () => ({ policy: { ...policy, parentOrigin: 'https://publisher.example.com/a' } })],
    ['unknown policy revision', () => ({ policy: { ...policy, policyRevision: 0 } })],
  ];

  it.each(ineligible)('does not expose a gesture or read when %s', (_label, change) => {
    const read = vi.fn();
    const p = props(read);
    render(<ManagedChannelEmbedShareControl {...p} {...change(p)} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
  });

  const transitions: Array<[string, (p: Props) => Partial<Props>]> = [
    ['owner', p => {
      const next = { ...scope, principalId: 'another-user' };
      return { scope: next, source: { ...p.source!, scope: next } };
    }],
    ['session', p => {
      const next = { ...scope, sessionFence: '9' };
      return { scope: next, source: { ...p.source!, scope: next } };
    }],
    ['workspace', p => {
      const next = { ...scope, workspaceId: 'other-workspace' };
      return { scope: next, source: { ...p.source!, scope: next } };
    }],
    ['provision', () => ({ channel: { ...channel, provision_id: 'different-install' } })],
    ['epoch', () => ({ channel: { ...channel, route_epoch: '24' } })],
    ['publication', () => ({ publication: { ...publication, publishedVersion: 5 } })],
    ['profile assignment revision', () => ({ profileRevision: 2 })],
    ['parent origin', () => ({ policy: { ...policy, parentOrigin: 'https://new.example.com' } })],
    ['policy revision', () => ({ policy: { ...policy, policyRevision: 7 } })],
    ['unavailable status', () => ({ channel: { ...channel, state: 'unavailable' } })],
    ['closed within the caller', () => ({ canInteract: false })],
    ['reader replacement', p => ({ source: { ...p.source!, read: vi.fn() } })],
  ];

  it.each(transitions)('immediately hides proof and aborts unsettled reads on %s change', async (_label, change) => {
    const { calls, read } = deferredRead();
    const p = props(read);
    const next = { ...p, ...change(p) };
    const view = render(<ManagedChannelEmbedShareControl {...p} />);
    fireEvent.click(check());
    await act(async () => { calls[0].release(approve(calls[0].owner)); });
    expect(snippet()).toBeVisible();
    view.rerender(<ManagedChannelEmbedShareControl {...next} />);
    expect(snippet()).not.toBeInTheDocument();
    view.rerender(<ManagedChannelEmbedShareControl {...p} />);
    expect(snippet()).not.toBeInTheDocument();
    fireEvent.click(check());
    expect(calls[1].signal.aborted).toBe(false);
    view.rerender(<ManagedChannelEmbedShareControl {...next} />);
    expect(calls[1].signal.aborted).toBe(true);
    await act(async () => { calls[1].release(approve(calls[1].owner)); });
    expect(snippet()).not.toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('aborts at unmount and never repaints a result from the closed dialog', async () => {
    const { calls, read } = deferredRead();
    const view = render(<ManagedChannelEmbedShareControl {...props(read)} />);
    fireEvent.click(check());
    view.unmount();
    expect(calls[0].signal.aborted).toBe(true);
    await act(async () => { calls[0].release(approve(calls[0].owner)); });
    expect(snippet()).not.toBeInTheDocument();
  });

  it('invalidates on auth or workspace activity; older results cannot overwrite a newer checked proof', async () => {
    const { calls, read } = deferredRead();
    render(<ManagedChannelEmbedShareControl {...props(read)} />);
    fireEvent.click(check());
    act(() => window.dispatchEvent(new Event('integral:tokens-updated')));
    expect(calls[0].signal.aborted).toBe(true);
    fireEvent.click(check());
    const current = 'https://detached.example.com/embed/new-fixture';
    await act(async () => { calls[1].release(approve(calls[1].owner, current)); });
    expect(snippet()?.value).toContain(current);
    await act(async () => { calls[0].release(approve(calls[0].owner)); });
    expect(snippet()?.value).toContain(current);
    act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'integral.scope' })));
    expect(snippet()).not.toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('hides prior proof during explicit recheck and displays no code on a mismatched read', async () => {
    const { calls, read } = deferredRead();
    render(<ManagedChannelEmbedShareControl {...props(read)} />);
    fireEvent.click(check());
    await act(async () => { calls[0].release(approve(calls[0].owner)); });
    expect(snippet()).toBeVisible();
    fireEvent.click(check());
    expect(snippet()).not.toBeInTheDocument();
    await act(async () => { calls[1].release(approve({ ...calls[1].owner, sessionFence: '9' })); });
    expect(screen.getByRole('status')).toHaveTextContent('No verified embed code is available.');
    expect(snippet()).not.toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
  });

  it('does not surface transport diagnostics or a plain copy of a proof', async () => {
    const read = vi.fn<ManagedChannelEmbedShareReader>()
      .mockRejectedValueOnce(new Error('synthetic internal error'))
      .mockImplementationOnce(async owner => ({ ...approve(owner) }));
    render(<ManagedChannelEmbedShareControl {...props(read)} />);
    fireEvent.click(check());
    expect(await screen.findByText('No verified embed code is available.')).toBeVisible();
    expect(screen.queryByText('synthetic internal error')).not.toBeInTheDocument();
    fireEvent.click(check());
    expect(await screen.findByText('No verified embed code is available.')).toBeVisible();
    expect(snippet()).not.toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
  });
});
