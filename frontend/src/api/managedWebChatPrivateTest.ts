import { getApiBaseURL } from '../config';
import { getActiveScopeHeader } from './client';
import { getAccessToken, getSessionGeneration } from './session';

export interface ManagedWebChatPrivateTestOwner {
  principalId: string;
  workspaceId: string;
  sessionFence: string;
  provisionId: string;
  routeEpoch: string;
  profileKey: string;
  publishedVersion: number;
}

const scopedFields = ['kind', 'workspace_id', 'slug', 'provision_id', 'route_epoch', 'profile_key', 'published_version', 'reply'];
// These are Web Chat transport credentials, never suitable private test input or output.
const webChatCredential = /(?:iweb|iwes|iwcg|iwcs)_[A-Za-z0-9_-]{24,128}/;
const deadlineMs = 18_000;

function privateText(value: unknown, limit: number): value is string {
  return typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= limit &&
    !/[\p{Cc}\p{Cs}]/u.test(value.replace(/[\r\n\t]/g, '')) && !webChatCredential.test(value);
}

export function isPrivateWebChatTestMessage(text: unknown): text is string {
  return privateText(text, 1000);
}

function identity(value: string, max = 256): boolean {
  return typeof value === 'string' && value.length > 0 && value.length <= max &&
    value === value.trim() && !/[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(value);
}

/** One deliberate authenticated call. Never retries or persists test data. */
export async function runManagedWebChatPrivateTest(
  owner: Readonly<ManagedWebChatPrivateTestOwner>, text: string, signal: AbortSignal,
): Promise<string> {
  const unavailable = () => new Error('Could not verify the private Web Chat test');
  const captured = { ...owner };
  const scope = `ws:${captured.workspaceId}`;
  const generation = getSessionGeneration();
  const token = getAccessToken();
  const current = () => !signal.aborted && getSessionGeneration() === generation &&
    getAccessToken() === token && getActiveScopeHeader() === scope &&
    owner.principalId === captured.principalId && owner.workspaceId === captured.workspaceId &&
    owner.sessionFence === captured.sessionFence && owner.provisionId === captured.provisionId &&
    owner.routeEpoch === captured.routeEpoch && owner.profileKey === captured.profileKey &&
    owner.publishedVersion === captured.publishedVersion;
  if (!token || !current() || captured.sessionFence !== String(generation) ||
      !identity(captured.principalId) || !identity(captured.workspaceId) || !identity(captured.provisionId) ||
      !/^(?:0|[1-9][0-9]{0,19})$/.test(captured.routeEpoch) ||
      !identity(captured.profileKey, 128) || !Number.isSafeInteger(captured.publishedVersion) ||
      captured.publishedVersion < 1 || !isPrivateWebChatTestMessage(text)) throw unavailable();

  const controller = new AbortController();
  const abort = () => controller.abort();
  const deadline = globalThis.setTimeout(abort, deadlineMs);
  signal.addEventListener('abort', abort, { once: true });
  try {
    if (!current()) throw unavailable();
    const query = new URLSearchParams({ workspace_id: captured.workspaceId });
    const response = await fetch(`${getApiBaseURL()}/agentive/managed-channels/public_web_chat/test?${query}`, {
      method: 'POST',
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      headers: {
        Authorization: `Bearer ${token}`,
        'X-Integral-Scope': scope,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({
        provision_id: captured.provisionId, route_epoch: captured.routeEpoch,
        profile_key: captured.profileKey, published_version: captured.publishedVersion, text: text.trim(),
      }),
      signal: controller.signal,
    });
    if (!current() || controller.signal.aborted || response.status !== 200 ||
        response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json' ||
        !/(?:^|,)\s*no-store\s*(?:,|$)/i.test(response.headers.get('cache-control') ?? '')) throw unavailable();
    const result: unknown = await response.json();
    if (!current() || controller.signal.aborted || !result || typeof result !== 'object' || Array.isArray(result) ||
        Object.keys(result).length !== scopedFields.length ||
        !scopedFields.every(field => Object.prototype.hasOwnProperty.call(result, field))) throw unavailable();
    const data = result as Record<string, unknown>;
    if (data.kind !== 'private-web-chat-test-v1' || data.workspace_id !== captured.workspaceId || data.slug !== 'public_web_chat' ||
        data.provision_id !== captured.provisionId || data.route_epoch !== captured.routeEpoch || data.profile_key !== captured.profileKey ||
        data.published_version !== captured.publishedVersion || !privateText(data.reply, 4000)) throw unavailable();
    return data.reply;
  } catch {
    throw unavailable();
  } finally {
    signal.removeEventListener('abort', abort);
    globalThis.clearTimeout(deadline);
  }
}
