/** Bridge injected by the Electron preload script (desktop/src/preload.js).
 *  Absent in browser builds — every access must be optional. */
interface IntegralDesktopBridge {
  getApiUrl?: () => string | null;
  setRecentConversations?: (conversations: DesktopRecentConversation[]) => void;
  syncNativeNotifications?: (snapshot: DesktopNotificationSnapshot) => void;
  getDesktopEnvironmentConfig?: () => DesktopEnvironmentConfig;
  connectDesktopEnvironment?: (session: DesktopEnvironmentSession) => Promise<boolean>;
  getDesktopEnvironmentBindingId?: () => string | null;
  onDesktopEnvironmentDisconnected?: (callback: () => void) => () => void;
  /** OS platform from the Electron shell (`process.platform`); absent in browsers. */
  platform?: string;
}

export interface DesktopRecentConversation {
  id: string;
  title: string;
  active: boolean;
}

export interface DesktopNotificationSnapshot {
  notifications: Array<{
    id: string;
    body: string;
    route: string;
    unread: boolean;
  }>;
  unreadCount: number;
}

export interface DesktopEnvironmentConfig {
  enabled: boolean;
  deviceId?: string;
  deviceName?: string;
  bindingId?: string | null;
  roots?: Array<{ id: string; label: string }>;
}

export interface DesktopEnvironmentSession {
  ticket: string;
  websocket_path: string;
  expires_in: number;
  capabilities: string[];
}

declare global {
  interface Window {
    integralDesktop?: IntegralDesktopBridge;
  }
}

const STORAGE_KEY = 'integral.api_url';

function trimTrailingSlashes(value: string): string {
  return value.replace(/\/+$/, '');
}

/** Backend origin without path (e.g. `http://localhost:4000`); empty → same-origin `/api`.
 *
 *  Resolution order (first non-empty wins):
 *  1. Electron preload bridge (`desktop/src/preload.js`) — runtime backend URL
 *     for the packaged app, where `file://` has no meaningful same-origin.
 *  2. `localStorage["integral.api_url"]` — manual override (works in both
 *     browser and desktop; lets a desktop user point at a remote backend).
 *  3. `VITE_API_URL` / `VITE_BACKEND_URL` build-time env (both accepted —
 *     older modules read `VITE_BACKEND_URL`).
 *  4. Same-origin fallback (Vite dev proxy, nginx, Traefik). */
export function getBackendOrigin(): string {
  try {
    const fromBridge = window?.integralDesktop?.getApiUrl?.();
    if (fromBridge && fromBridge.trim()) return trimTrailingSlashes(fromBridge.trim());
  } catch {
    // Bridge unavailable (plain browser) — fall through.
  }
  try {
    const fromStorage = localStorage.getItem(STORAGE_KEY);
    if (fromStorage && fromStorage.trim()) return trimTrailingSlashes(fromStorage.trim());
  } catch {
    // Storage unavailable (SSR/test) — fall through.
  }
  const envOrigin =
    import.meta.env.VITE_API_URL || import.meta.env.VITE_BACKEND_URL || '';
  return trimTrailingSlashes(String(envOrigin));
}

/** Persist / clear the manual backend-origin override (see resolution order). */
export function setBackendOriginOverride(url: string | null): void {
  try {
    if (url && url.trim()) localStorage.setItem(STORAGE_KEY, trimTrailingSlashes(url.trim()));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable — override silently not persisted.
  }
}

/** OS platform when running inside the Electron shell, else null. */
export function getDesktopPlatform(): string | null {
  try {
    return window?.integralDesktop?.platform ?? null;
  } catch {
    return null;
  }
}

export function syncDesktopRecentConversations(
  conversations: DesktopRecentConversation[],
): void {
  try {
    window?.integralDesktop?.setRecentConversations?.(conversations);
  } catch {
    // Desktop presentation is best-effort and must never break chat.
  }
}

export function syncDesktopNotifications(snapshot: DesktopNotificationSnapshot): void {
  try {
    window?.integralDesktop?.syncNativeNotifications?.(snapshot);
  } catch {
    // Desktop presentation is best-effort and must never break notifications.
  }
}

export function getDesktopEnvironmentConfig(): DesktopEnvironmentConfig | null {
  try {
    return window?.integralDesktop?.getDesktopEnvironmentConfig?.() ?? null;
  } catch {
    return null;
  }
}

export function getDesktopEnvironmentBindingId(): string | null {
  try {
    return window?.integralDesktop?.getDesktopEnvironmentBindingId?.() ?? null;
  } catch {
    return null;
  }
}

export async function connectDesktopEnvironment(
  session: DesktopEnvironmentSession,
): Promise<boolean> {
  try {
    return (await window?.integralDesktop?.connectDesktopEnvironment?.(session)) ?? false;
  } catch {
    return false;
  }
}

export function onDesktopEnvironmentDisconnected(callback: () => void): () => void {
  try {
    return window?.integralDesktop?.onDesktopEnvironmentDisconnected?.(callback) ?? (() => {});
  } catch {
    return () => {};
  }
}

/** Height (px) of the shell's mimic title bar — the transparent drag strip
 *  under the floating macOS traffic lights. Keep in lockstep with
 *  `trafficLightPosition` in `desktop/src/main.js` (lights at y=16 need a
 *  strip comfortably taller than 28px). Consumers never hardcode this;
 *  `hasDesktopTitlebar()` gates it and `--desktop-titlebar-h` carries it. */
export const DESKTOP_TITLEBAR_PX = 40;

/** True when the shell hides the native title bar (macOS only, for now):
 *  the app must reserve {@link DESKTOP_TITLEBAR_PX}px of top chrome and
 *  provide a window drag surface there. */
export function hasDesktopTitlebar(): boolean {
  try {
    return isDesktop() && getDesktopPlatform() === 'darwin';
  } catch {
    return false;
  }
}

/** True inside the Electron shell (preload bridge) or under `file://`. */
export function isDesktop(): boolean {
  try {
    if (window?.integralDesktop) return true;
    if (typeof window !== 'undefined' && window.location.protocol === 'file:') return true;
  } catch {
    // ignore
  }
  return false;
}

export function getApiBaseURL(): string {
  // Re-resolve on every call (not just at module load) so a runtime
  // override via the desktop bridge / localStorage takes effect without
  // a rebuild. The axios instance captures the module-load value as its
  // default, so callers that need the live value (raw fetch/SSE paths)
  // must call this function per request.
  const live = getBackendOrigin();
  return live ? `${live}/api` : '/api';
}

/** Build an absolute ws(s):// URL for a backend socket path (e.g. `/ws/agent-events`).
 *  Extra query params are appended; values are URL-encoded. In same-origin
 *  browser setups this resolves against the page host; in desktop (or when a
 *  backend origin is configured) it resolves against the backend origin. */
export function getWebSocketUrl(
  path: string,
  params?: Record<string, string>,
): string {
  const origin = getBackendOrigin();
  const query = params
    ? Object.entries(params)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join('&')
    : '';
  const suffix = query ? `?${query}` : '';
  if (origin) {
    const wsBase = origin.replace(/^http/i, (m) =>
      m.toLowerCase() === 'https' ? 'wss' : 'ws',
    );
    return `${wsBase}${path}${suffix}`;
  }
  const protocol =
    typeof window !== 'undefined' && window.location.protocol === 'https:'
      ? 'wss:'
      : 'ws:';
  const host = typeof window !== 'undefined' ? window.location.host : 'localhost';
  return `${protocol}//${host}${path}${suffix}`;
}
