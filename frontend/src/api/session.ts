/**
 * Session lifecycle: token storage, sliding refresh, activity tracking.
 *
 * Lives outside React because the axios interceptor calls into it from
 * non-component code and would deadlock against React state. The
 * AuthContext subscribes to recovery state for in-place renewal and to
 * `integral:logout` for explicit sign-out.
 *
 * Behavior summary:
 *   - Proactive refresh fires `(expires_in - 120)s` after each token issue.
 *   - On fire, refresh runs only with recent human activity (at most
 *     five minutes, bounded by the access TTL); otherwise access expires.
 *   - Expiry pauses an open page behind a renewal dialog. A rejected
 *     refresh requires credentials; temporary outages remain retryable.
 *   - 401 reactive retry: a single retry per request, gated on a
 *     `__sessionRetried` flag on the axios config.
 *
 * Activity counts as: mousedown, keydown, touchstart, scroll, wheel,
 * click, and visibilitychange→visible. Raw `mousemove` is excluded so
 * pointer drift / cats on keyboards do not keep sessions alive.
 */

import axios, { AxiosError } from 'axios';
import { getApiBaseURL } from '../config';
import { isSessionPaused, requestSessionRecovery } from './sessionRecovery';

const ACCESS_TOKEN_KEY = 't75_token';
const REFRESH_TOKEN_KEY = 't75_refresh_token';
const USER_KEY = 't75_user';
const ACCESS_EXPIRES_AT_KEY = 't75_token_expires_at';

/**
 * User-scoped localStorage keys that must be wiped on logout / 401 so
 * the next signed-in user never inherits the previous user's state.
 *
 * Excludes device-level prefs (theme) — those persist across users on
 * the same browser by design.
 *
 * Add to this list whenever a new module persists user-bound state
 * under its own key (workspace scope, settings, preferences, etc.).
 */
const USER_SCOPED_KEYS = [
  'integral.scope', // ScopeContext — active workspace id
  'integral.settings.v1', // features/settings/store.ts — user prefs
];

/** Refresh fires this many seconds before access-token expiry. */
const REFRESH_LEAD_SECONDS = 120;
/** Minimum gap between proactive refreshes (guards against very short TTLs). */
const MIN_REFRESH_INTERVAL_SECONDS = 30;
/** Activity stamps are throttled to one write per this many ms. */
const ACTIVITY_THROTTLE_MS = 5_000;
/** Fallback access TTL when issuance metadata is unavailable. */
const DEFAULT_ACCESS_TTL_MS = 30 * 60 * 1000;
/** Only recently interacting users qualify for a silent renewal. */
const SILENT_REFRESH_ACTIVITY_MS = 5 * 60 * 1000;

export interface TokenPayload {
  access_token: string;
  token_type?: string;
  expires_in?: number;
  refresh_token?: string | null;
  refresh_expires_in?: number | null;
}

let lastActivityAt = Date.now();
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | null = null;
let inflightRefresh: Promise<string | null> | null = null;
let sessionGeneration = 0;

export const getSessionGeneration = (): number => sessionGeneration;
/** Most recent access-token TTL in ms, captured at issuance. */
let currentAccessTtlMs = DEFAULT_ACCESS_TTL_MS;

// ────────────────────────────────────────────────────────────────────
// Token storage
// ────────────────────────────────────────────────────────────────────

export function getAccessToken(): string | null {
  if (isSessionPaused()) return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(payload: TokenPayload): void {
  if (!payload.access_token) return;
  localStorage.setItem(ACCESS_TOKEN_KEY, payload.access_token);
  if (payload.refresh_token) {
    localStorage.setItem(REFRESH_TOKEN_KEY, payload.refresh_token);
  }
  if (typeof payload.expires_in === 'number' && payload.expires_in > 0) {
    currentAccessTtlMs = payload.expires_in * 1000;
    const expiresAt = Date.now() + currentAccessTtlMs;
    localStorage.setItem(ACCESS_EXPIRES_AT_KEY, String(expiresAt));
  } else {
    localStorage.removeItem(ACCESS_EXPIRES_AT_KEY);
  }
  scheduleProactiveRefresh();
}

/**
 * Wipe every localStorage key that belongs to the current signed-in
 * user — tokens, cached User, and any module-owned user-scoped state
 * (workspace scope, settings, etc. — see ``USER_SCOPED_KEYS``).
 *
 * Called on explicit logout AND on 401 refresh failure. The next
 * sign-in must start from a clean per-user slate; otherwise the new
 * user inherits the previous user's active workspace, preferences,
 * etc. (the workspace-switcher-shows-old-workspace bug).
 *
 * Device-level prefs (theme) are deliberately preserved.
 */
export function clearTokens(): void {
  sessionGeneration += 1;
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(ACCESS_EXPIRES_AT_KEY);
  for (const key of USER_SCOPED_KEYS) {
    localStorage.removeItem(key);
  }
  cancelScheduledRefresh();
}

/** True while a refresh token exchange is in flight (single-flight). */
export function isRefreshInFlight(): boolean {
  return inflightRefresh !== null;
}

/**
 * Require reauthentication in place, or land on /login during cold boot.
 *
 * Invalid credentials are removed immediately. A mounted account retains
 * its page state behind the session lock; it cannot make authenticated calls.
 */
export function redirectToLogin(reason: 'session_expired' = 'session_expired'): void {
  if (typeof window !== 'undefined' && !isPublicAuthPath(window.location.pathname)
      && requestSessionRecovery('signin')) {
    sessionGeneration += 1;
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    cancelScheduledRefresh();
    return;
  }
  clearTokens();
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent('integral:logout'));
  if (isPublicAuthPath(window.location.pathname)) return;
  const params = new URLSearchParams({ reason });
  window.location.href = `/login?${params.toString()}`;
}

function isPublicAuthPath(pathname: string): boolean {
  return (
    pathname.includes('/login') ||
    pathname.includes('/signup') ||
    pathname.includes('/forgot-password') ||
    pathname.includes('/reset-password') ||
    pathname.includes('/verify-email') ||
    pathname.startsWith('/invitations/') ||
    pathname.startsWith('/portfolio/shared/') ||
    pathname.startsWith('/public/tracks/')
  );
}

// ────────────────────────────────────────────────────────────────────
// Activity tracking
// ────────────────────────────────────────────────────────────────────

export function markActivity(): void {
  const now = Date.now();
  if (now - lastActivityAt > ACTIVITY_THROTTLE_MS) {
    lastActivityAt = now;
  }
}

export function wasActiveWithin(windowMs: number): boolean {
  return Date.now() - lastActivityAt <= windowMs;
}

let activityListenersInstalled = false;

export function installActivityListeners(): void {
  if (activityListenersInstalled || typeof window === 'undefined') return;
  activityListenersInstalled = true;
  const opts: AddEventListenerOptions = { passive: true, capture: true };
  const events: (keyof WindowEventMap)[] = [
    'mousedown',
    'keydown',
    'touchstart',
    'scroll',
    'wheel',
    'click',
  ];
  events.forEach(name => window.addEventListener(name, markActivity, opts));
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { checkSessionDeadline(); markActivity(); }
  });
}

// ────────────────────────────────────────────────────────────────────
// Refresh scheduling
// ────────────────────────────────────────────────────────────────────

export function cancelScheduledRefresh(): void {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
  if (expiryTimer) {
    clearTimeout(expiryTimer);
    expiryTimer = null;
  }
}

/** Pause in place after expiry or a temporary refresh failure; keep drafts in memory. */
export function pauseForSessionRenewal(): void {
  if (requestSessionRecovery(getRefreshToken() ? 'renew' : 'signin')) {
    cancelScheduledRefresh();
  } else {
    redirectToLogin();
  }
}

/** Also checked before requests and on tab wake-up, since timers can be throttled. */
export function checkSessionDeadline(): void {
  const expiresAt = Number(localStorage.getItem(ACCESS_EXPIRES_AT_KEY));
  if (!isSessionPaused() && expiresAt > 0 && expiresAt <= Date.now()) pauseForSessionRenewal();
}

/**
 * Compute the seconds-from-now at which to fire the next proactive
 * refresh. Reads `t75_token_expires_at` if available; otherwise falls
 * back to the configured TTL.
 */
function computeDelaySeconds(): number {
  const rawExpiry = localStorage.getItem(ACCESS_EXPIRES_AT_KEY);
  if (rawExpiry) {
    const expiresAt = Number(rawExpiry);
    if (Number.isFinite(expiresAt)) {
      const secondsUntilExpiry = Math.floor((expiresAt - Date.now()) / 1000);
      return Math.max(
        MIN_REFRESH_INTERVAL_SECONDS,
        secondsUntilExpiry - REFRESH_LEAD_SECONDS,
      );
    }
  }
  return Math.max(
    MIN_REFRESH_INTERVAL_SECONDS,
    Math.floor(currentAccessTtlMs / 1000) - REFRESH_LEAD_SECONDS,
  );
}

export function scheduleProactiveRefresh(): void {
  cancelScheduledRefresh();
  if (!getAccessToken()) return;
  const expiresAt = Number(localStorage.getItem(ACCESS_EXPIRES_AT_KEY));
  if (expiresAt > 0 && Number.isFinite(expiresAt)) {
    expiryTimer = setTimeout(pauseForSessionRenewal, Math.max(0, expiresAt - Date.now()));
  }
  if (!getRefreshToken()) return;
  const delayMs = computeDelaySeconds() * 1000;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    // Background polling is not human activity. Let idle access expire
    // and present the renewal prompt rather than refreshing indefinitely.
    if (!wasActiveWithin(Math.min(currentAccessTtlMs, SILENT_REFRESH_ACTIVITY_MS))) return;
    refreshAccessToken().then(token => {
      if (!token && !isSessionPaused()) pauseForSessionRenewal();
    });
  }, delayMs);
}

// ────────────────────────────────────────────────────────────────────
// Refresh call (single-flight)
// ────────────────────────────────────────────────────────────────────

/**
 * Bare axios call — bypasses `apiClient` interceptors to avoid the
 * recursion of "401 on refresh → trigger refresh → 401 → …".
 */
async function callRefreshEndpoint(refreshToken: string): Promise<TokenPayload> {
  const res = await axios.post<TokenPayload>(
    `${getApiBaseURL()}/auth/refresh`,
    { refresh_token: refreshToken },
    { headers: { 'Content-Type': 'application/json' }, timeout: 20_000 },
  );
  return res.data;
}

/**
 * Exchange the stored refresh token for a fresh access token. Returns
 * the new access token on success, or `null` on failure. The caller
 * should not retry; an authoritative rejection opens the sign-in prompt.
 */
export function refreshAccessToken(): Promise<string | null> {
  if (inflightRefresh) return inflightRefresh;
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return Promise.resolve(null);
  }
  const generation = sessionGeneration;

  inflightRefresh = (async () => {
    try {
      const payload = await callRefreshEndpoint(refreshToken);
      if (generation !== sessionGeneration || getRefreshToken() !== refreshToken) return null;
      if (!payload?.access_token) {
        throw new Error('Refresh response missing access_token');
      }
      setTokens(payload);
      return payload.access_token;
    } catch (err) {
      const status = (err as AxiosError | undefined)?.response?.status;
      // Authentication rejection removes invalid credentials. Rate limits,
      // 5xx and network failures retain credentials for an explicit retry.
      if (generation === sessionGeneration && getRefreshToken() === refreshToken
          && (status === 400 || status === 401 || status === 403)) {
        redirectToLogin();
      }
      return null;
    } finally {
      inflightRefresh = null;
    }
  })();

  return inflightRefresh;
}
