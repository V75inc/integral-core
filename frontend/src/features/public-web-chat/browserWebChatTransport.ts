import type { PublicWebChatTransport, VisitorMessage } from './PublicWebChat';

const MAX_MESSAGES = 16;
const MAX_HISTORY_CHARACTERS = 8_000;

type Reply =
  | { kind: 'ready'; messages: readonly VisitorMessage[] }
  | { kind: 'unavailable' | 'expired' | 'pending' };

function isOpaqueSegment(value: string): boolean {
  return value.length > 0 && value.length <= 128 && !/[^A-Za-z0-9_-]/.test(value);
}

function exactObject(value: unknown, fields: readonly string[]): value is Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === fields.length &&
    fields.every(key => Object.prototype.hasOwnProperty.call(value, key));
}

function readReply(input: unknown, allowPending: boolean): Reply {
  if (exactObject(input, ['kind']) && (
    input.kind === 'unavailable' || input.kind === 'expired' || (allowPending && input.kind === 'pending')
  )) return { kind: input.kind };

  if (!exactObject(input, ['kind', 'messages']) || input.kind !== 'ready' ||
    !Array.isArray(input.messages) || input.messages.length > MAX_MESSAGES) {
    throw new Error('Invalid Web Chat response');
  }

  const seen = new Set<string>();
  let characters = 0;
  const messages: VisitorMessage[] = [];
  for (const message of input.messages) {
    if (!exactObject(message, ['id', 'role', 'text']) ||
      typeof message.id !== 'string' || !isOpaqueSegment(message.id) || seen.has(message.id) ||
      (message.role !== 'visitor' && message.role !== 'assistant') ||
      typeof message.text !== 'string' || !message.text.trim()) {
      throw new Error('Invalid Web Chat response');
    }
    const length = Array.from(message.text).length;
    characters += length;
    if (length > (message.role === 'visitor' ? 1_000 : 4_000) || characters > MAX_HISTORY_CHARACTERS) {
      throw new Error('Invalid Web Chat response');
    }
    seen.add(message.id);
    messages.push({ id: message.id, role: message.role, text: message.text });
  }
  return { kind: 'ready', messages };
}

/** Public full-page client. The host supplies an opaque route, not an inferred profile or API URL. */
export function createBrowserWebChatTransport(
  route: string,
  fetcher: typeof fetch = globalThis.fetch.bind(globalThis),
): PublicWebChatTransport {
  if (!isOpaqueSegment(route)) throw new Error('Invalid Web Chat route');
  const base = `/api/public/web-chat/${route}`;
  async function post(
    endpoint: 'session' | 'messages',
    payload: Record<string, string>,
    signal: AbortSignal,
  ): Promise<Reply> {
    const response = await fetcher(`${base}/${endpoint}`, {
      method: 'POST',
      credentials: 'same-origin',
      mode: 'same-origin',
      redirect: 'error',
      cache: 'no-store',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    });
    if (response.status === 403 || response.status === 404) return { kind: 'unavailable' };
    if (!response.ok || response.headers.get('content-type')?.split(';')[0]?.trim() !== 'application/json') {
      throw new Error('Web Chat request could not be completed');
    }
    return readReply(await response.json(), endpoint === 'messages');
  }
  return {
    async start({ mode, signal }) {
      const reply = await post('session', { mode }, signal);
      switch (reply.kind) {
        case 'ready': return reply;
        case 'unavailable': return { kind: 'unavailable' };
        case 'expired': return { kind: 'expired' };
        default: throw new Error('Invalid Web Chat response');
      }
    },
    async send({ id, text, signal }) {
      if (!isOpaqueSegment(id) || !text.trim() || Array.from(text).length > 1_000) {
        throw new Error('Invalid Web Chat message');
      }
      const reply = await post('messages', { id, text }, signal);
      switch (reply.kind) {
        case 'ready': return reply;
        case 'unavailable': return { kind: 'unavailable' };
        case 'expired': return { kind: 'expired' };
        case 'pending': return { kind: 'pending' };
      }
    },
  };
}
