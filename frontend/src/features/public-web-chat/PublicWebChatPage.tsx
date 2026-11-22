import { useMemo } from 'react';

import { Surface, Text } from '../../ui';
import { createBrowserWebChatTransport, isOpaqueWebChatSegment } from './browserWebChatTransport';
import { PublicWebChat } from './PublicWebChat';

interface FullPageConfig {
  route: string;
  title: string;
}

const MAX_TITLE_CHARACTERS = 120;

function readHostConfig(raw: unknown): FullPageConfig | null {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw) ||
      Object.keys(raw).length !== 2 ||
      !Object.prototype.hasOwnProperty.call(raw, 'route') ||
      !Object.prototype.hasOwnProperty.call(raw, 'title')) return null;
  const { route, title } = raw as Record<string, unknown>;
  if (typeof route !== 'string' || !isOpaqueWebChatSegment(route) ||
      typeof title !== 'string' || !title.trim() ||
      Array.from(title).length > MAX_TITLE_CHARACTERS ||
      /[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(title)) return null;
  return { route, title };
}

/**
 * Detached full-page composition for a reviewed host. Only pass server-issued
 * public {route, title}; do not infer configuration from location, settings,
 * credentials or browser storage. The HTTPS host and API enforce admission.
 */
export function PublicWebChatPage({ config }: { config: unknown }) {
  const admitted = readHostConfig(config);
  const route = admitted?.route;
  const transport = useMemo(
    () => route && typeof globalThis.fetch === 'function' ? createBrowserWebChatTransport(route) : null,
    [route],
  );

  if (admitted && transport) {
    // A different route is a different visitor conversation, even during a
    // slow request. Unmount the prior renderer, runtime, and request owner.
    return <PublicWebChat key={admitted.route} title={admitted.title} transport={transport} />;
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-3 px-4 py-4 sm:gap-5 sm:px-6 sm:py-6">
      <Text as="h1" variant="heading-md" weight="semibold">Web Chat</Text>
      <Surface border="subtle" radius="card" padding="md">
        <Text as="p" role="status">This chat is unavailable.</Text>
      </Surface>
    </main>
  );
}
