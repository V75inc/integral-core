import { isOpaqueWebChatSegment } from './browserWebChatTransport';
import type { PublicWebChatTransport, VisitorMessage } from './PublicWebChat';

const BOOT_SEED = /^iweb_[A-Za-z0-9_-]{32,128}$/;
const SESSION_TOKEN = /^iwes_[A-Za-z0-9_-]{32,128}$/;
const CREDENTIAL_TEXT = /(?:iweb|iwes)_[A-Za-z0-9_-]{32,128}/;
export function containsEmbedCredential(value: string): boolean {
  return CREDENTIAL_TEXT.test(value);
}

const MAX_SESSION_SECONDS = 300;
const MAX_HISTORY_MESSAGES = 16;
const MAX_HISTORY_CHARACTERS = 8_000;
const MAX_VISITOR_CHARACTERS = 1_000;
const MAX_ASSISTANT_CHARACTERS = 4_000;

interface Browser {
  self: object;
  top: object | null;
  location: Pick<Location, 'origin'>;
  fetch: typeof fetch;
}
type FailureReply = { kind: 'expired' } | { kind: 'unavailable' };
type ChatReply = { kind: 'ready'; messages: VisitorMessage[] } | FailureReply | { kind: 'pending' };
type ReadySession = { reply: Extract<ChatReply, { kind: 'ready' }>; token: string; expiresIn: number };
type EmbeddedSession = { token: string; deadline: number };

function unavailable(): Error {
  // Never include payload, credential, response details, or transport causes.
  return new Error('Embedded Web Chat request could not be completed');
}

function matches(value: unknown, fields: readonly string[]): value is Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === fields.length &&
    fields.every(field => Object.prototype.hasOwnProperty.call(value, field));
}

function exactOrigin(value: unknown): value is string {
  if (typeof value !== 'string' || !/^https:\/\/[a-z0-9.-]+(?::[0-9]{1,5})?$/.test(value)) return false;
  try {
    const parsed = new URL(value);
    return parsed.origin === value && !parsed.username && !parsed.password &&
      parsed.hostname !== 'localhost' && !parsed.hostname.endsWith('.localhost') &&
      !parsed.hostname.startsWith('127.') && parsed.hostname !== '0.0.0.0' &&
      parsed.hostname.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) &&
      parsed.port !== '0';
  } catch {
    return false;
  }
}

function inFrame(browser: Browser, origin: string): boolean {
  // Comparing WindowProxy identities does not inspect the parent. The iframe
  // origin is not evidence of who framed it; host CSP must enforce that.
  return Boolean(browser.top && browser.self !== browser.top && browser.location.origin === origin);
}

function messageText(value: unknown, limit: number): value is string {
  return typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= limit &&
    !/[\p{Cc}\p{Cs}]/u.test(value.replace(/[\r\n\t]/g, '')) && !containsEmbedCredential(value);
}

function messages(input: unknown): VisitorMessage[] {
  if (!Array.isArray(input) || input.length > MAX_HISTORY_MESSAGES) throw unavailable();
  const found = new Set<string>();
  let characters = 0;
  const result: VisitorMessage[] = [];
  for (const item of input) {
    if (!matches(item, ['id', 'role', 'text']) || typeof item.id !== 'string' ||
        !isOpaqueWebChatSegment(item.id) || containsEmbedCredential(item.id) || found.has(item.id) ||
        (item.role !== 'visitor' && item.role !== 'assistant') ||
        !messageText(item.text, item.role === 'visitor' ? MAX_VISITOR_CHARACTERS : MAX_ASSISTANT_CHARACTERS)) {
      throw unavailable();
    }
    found.add(item.id);
    characters += Array.from(item.text).length;
    if (characters > MAX_HISTORY_CHARACTERS) throw unavailable();
    result.push({ id: item.id, role: item.role, text: item.text });
  }
  return result;
}

function failure(input: unknown): FailureReply | null {
  if (matches(input, ['kind']) && input.kind === 'expired') return { kind: 'expired' };
  if (matches(input, ['kind']) && input.kind === 'unavailable') return { kind: 'unavailable' };
  return null;
}

function decodeSession(input: unknown): ReadySession | FailureReply {
  const closed = failure(input);
  if (closed) return closed;
  if (!matches(input, ['kind', 'messages', 'session_token', 'expires_in']) || input.kind !== 'ready' ||
      typeof input.session_token !== 'string' || !SESSION_TOKEN.test(input.session_token) ||
      typeof input.expires_in !== 'number' || !Number.isSafeInteger(input.expires_in) ||
      input.expires_in < 1 || input.expires_in > MAX_SESSION_SECONDS) throw unavailable();
  return {
    reply: { kind: 'ready', messages: messages(input.messages) },
    token: input.session_token,
    expiresIn: input.expires_in,
  };
}

function decodeTurn(input: unknown): ChatReply {
  const closed = failure(input);
  if (closed) return closed;
  if (matches(input, ['kind']) && input.kind === 'pending') return { kind: 'pending' };
  if (!matches(input, ['kind', 'messages']) || input.kind !== 'ready') throw unavailable();
  return { kind: 'ready', messages: messages(input.messages) };
}

/**
 * Detached transport for a separately approved embed host. Config contains
 * exactly the server-projected route, embed origin and one-use bootstrap seed.
 * No parent messaging, cookies, storage or credentials in URLs. It is not
 * mounted or used by the full-page transport or default build.
 *
 * The server must inject a fresh short-lived iweb_ proof into the admitted
 * HTML. The caller must take that configuration out of the DOM before
 * rendering and never log or persist it. Authenticate and consume the proof
 * atomically against an exact live embed
 * policy/route/publication; issue only a fresh iwes_ bearer with <=300s TTL;
 * recheck audience, route, origin policy and expiry on every request. Secure
 * CSP frame-ancestors and a distinct vetted origin protect the document; the
 * same-origin HTTP calls do not identify the parent or grant authority.
 *
 * Session/bearer state is private to this JS instance. A reload requires a
 * fresh host-issued proof and starts an empty conversation. There is no retry
 * of a spent bootstrap or an ambiguous New request. No-store applies to the
 * rendered embed document and both API responses (including errors).
 */
export function createEmbedWebChatTransport(
  config: unknown,
  browser: Browser | null = typeof window === 'undefined' ? null : window,
  now: () => number = () => performance.now(),
): PublicWebChatTransport | null {
  if (!browser || !matches(config, ['route', 'origin', 'bootstrapSeed']) ||
      typeof config.route !== 'string' || !isOpaqueWebChatSegment(config.route) || containsEmbedCredential(config.route) ||
      !exactOrigin(config.origin) || !inFrame(browser, config.origin) ||
      typeof config.bootstrapSeed !== 'string' || !BOOT_SEED.test(config.bootstrapSeed)) return null;

  const runtime = browser;
  const origin = config.origin;
  const base = `/api/public/web-chat-embed/${config.route}`;
  let seed: string | null = config.bootstrapSeed;
  let session: EmbeddedSession | null = null;
  let inflight = false;

  function before(signal: AbortSignal): void {
    if (!inFrame(runtime, origin)) {
      seed = null;
      session = null;
      throw unavailable();
    }
    if (inflight || signal.aborted || !Number.isFinite(now())) throw unavailable();
    if (session && now() >= session.deadline) session = null;
  }

  async function post(endpoint: 'session' | 'messages', payload: Record<string, string>,
    token: string | null, signal: AbortSignal): Promise<unknown> {
    try {
      const response = await runtime.fetch(`${base}/${endpoint}`, {
        method: 'POST',
        credentials: 'omit',
        mode: 'same-origin',
        redirect: 'error',
        cache: 'no-store',
        referrerPolicy: 'no-referrer',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
        signal,
      });
      if (signal.aborted) throw unavailable();
      if (response.status === 401 || response.status === 410) return { kind: 'expired' };
      if (response.status === 403 || response.status === 404) return { kind: 'unavailable' };
      if (!response.ok || response.headers.get('content-type')?.split(';')[0].trim() !== 'application/json' ||
          !/(?:^|,)\s*no-store\s*(?:,|$)/i.test(response.headers.get('cache-control') ?? '')) throw unavailable();
      const result: unknown = await response.json();
      if (signal.aborted || !inFrame(runtime, origin)) throw unavailable();
      return result;
    } catch {
      throw unavailable();
    }
  }

  return {
    async start({ mode, signal }) {
      before(signal);
      if (mode !== 'new' && mode !== 'resume') throw unavailable();
      const token = session?.token ?? null;
      const initial = token ? null : seed;
      if (!token && !initial) return { kind: 'expired' };
      // A consumed proof cannot be replayed, including after an uncertain
      // response on the network. New rotates instead of restoring old history.
      seed = null;
      if (mode === 'new') session = null;
      inflight = true;
      const started = now();
      try {
        const result = decodeSession(await post('session', initial ? { mode, bootstrap_seed: initial } : { mode }, token, signal));
        if ('kind' in result) {
          session = null;
          return result;
        }
        if ((initial || mode === 'new') && result.reply.messages.length > 0) throw unavailable();
        if (mode === 'new' && result.token === token) throw unavailable();
        const deadline = started + result.expiresIn * 1000;
        if (!Number.isFinite(deadline) || now() >= deadline) {
          session = null;
          return { kind: 'expired' };
        }
        session = { token: result.token, deadline };
        return result.reply;
      } finally {
        inflight = false;
      }
    },
    async send({ id, text, signal }) {
      before(signal);
      if (!isOpaqueWebChatSegment(id) || containsEmbedCredential(id) || !messageText(text, MAX_VISITOR_CHARACTERS)) throw unavailable();
      const authorized = session;
      if (!authorized) return { kind: 'expired' };
      inflight = true;
      try {
        const result = decodeTurn(await post('messages', { id, text }, authorized.token, signal));
        if (result.kind === 'expired' || result.kind === 'unavailable') session = null;
        if (now() >= authorized.deadline) {
          session = null;
          return { kind: 'expired' };
        }
        return result;
      } finally {
        inflight = false;
      }
    },
  };
}
