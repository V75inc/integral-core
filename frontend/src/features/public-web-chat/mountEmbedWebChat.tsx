import { createRoot } from 'react-dom/client';

import { Surface, Text } from '../../ui';
import { containsEmbedCredential, createEmbedWebChatTransport } from './embedWebChatTransport';
import { PublicWebChat } from './PublicWebChat';

const ROOT_ID = 'public-web-chat-embed-root';
const PREFIX = 'data-web-chat-embed-';
const CONFIG = {
  route: `${PREFIX}route`,
  origin: `${PREFIX}origin`,
  bootstrapSeed: `${PREFIX}bootstrap-seed`,
  title: `${PREFIX}title`,
} as const;
const allowed = new Set<string>(Object.values(CONFIG));
const started = new WeakSet<Element>();

function extractAndErase(container: Element) {
  const fields = container.getAttributeNames().filter(name => name.startsWith(PREFIX));
  const exact = fields.length === allowed.size && fields.every(name => allowed.has(name));
  const route = container.getAttribute(CONFIG.route);
  const origin = container.getAttribute(CONFIG.origin);
  const bootstrapSeed = container.getAttribute(CONFIG.bootstrapSeed);
  const title = container.getAttribute(CONFIG.title);
  // Remove the seed (and any unexpected embed config) before any validation,
  // rendering or network use. This does not erase the delivered HTTP source.
  fields.forEach(name => container.removeAttribute(name));
  if (!exact || !title || !title.trim() || Array.from(title).length > 120 ||
      /[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(title) || containsEmbedCredential(title)) return null;
  const transport = createEmbedWebChatTransport({ route, origin, bootstrapSeed }, container.ownerDocument.defaultView);
  return transport ? { title, transport } : null;
}

/**
 * Standalone iframe artifact only. HTML attributes deliver but do not
 * authorize configuration: the HTTPS host and embed API own admission.
 * Caller supplies no fallback from URL, browser storage, or parent messages.
 */
export function mountEmbedWebChat(doc: Document) {
  const containers = doc.querySelectorAll(`[id="${ROOT_ID}"]`);
  if (!containers.length) return null;
  if (containers.length !== 1) {
    // Ambiguous DOM is not an admitted configuration; still scrub its fields.
    for (const node of containers) {
      node.getAttributeNames().filter(name => name.startsWith(PREFIX))
        .forEach(name => node.removeAttribute(name));
    }
    return null;
  }
  const [container] = containers;
  if (started.has(container)) return null;
  started.add(container);
  const admitted = extractAndErase(container);
  const root = createRoot(container);
  root.render(admitted
    ? <PublicWebChat title={admitted.title} transport={admitted.transport} restartMode="reopen-embed" />
    : <main className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col gap-3 px-4 py-4 sm:gap-5 sm:px-6 sm:py-6">
        <Text as="h1" variant="heading-md" weight="semibold">Web Chat</Text>
        <Surface border="subtle" radius="card" padding="md">
          <Text as="p" role="status">This chat is unavailable.</Text>
        </Surface>
      </main>);
  return root;
}
