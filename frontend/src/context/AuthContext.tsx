import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { resetThreadSessions } from '../features/ai-chat/threadSessionStore';
import { authApi } from '../api';
import {
  clearTokens,
  getAccessToken,
  installActivityListeners,
  redirectToLogin,
  refreshAccessToken,
  scheduleProactiveRefresh,
  setTokens,
} from '../api/session';
import type { User } from '../types';

interface AuthCtx {
  user: User | null; token: string | null; loading: boolean;
  login(email: string, password: string): Promise<void>;
  signup(
    email: string,
    password: string,
    display_name: string,
    workspaceName?: string
  ): Promise<void>;
  logout(): Promise<void>;
  refreshUser(): Promise<void>;
}
const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const queryClient = useQueryClient();

  useEffect(() => {
    installActivityListeners();

    const onLogout = () => {
      setToken(null);
      setUser(null);
      // 401 interceptor path — wipe every cached query so the next
      // signed-in user doesn't inherit the previous user's
      // workspaces / scope / notifications / etc.
      queryClient.clear();
    };
    window.addEventListener('integral:logout', onLogout);

    const t = getAccessToken();
    if (!t) { setLoading(false); return () => window.removeEventListener('integral:logout', onLogout); }
    setToken(t);
    const cached = localStorage.getItem('t75_user');
    if (cached) {
      try {
        setUser(JSON.parse(cached));
      } catch {
        // Corrupt cached user JSON — fall through to the network fetch below.
      }
    }
    // Kick off the proactive-refresh timer using whatever TTL was
    // captured at issuance (session.ts persists it to localStorage).
    scheduleProactiveRefresh();
    // Only an authoritative rejection (401/403) means the session is gone.
    // Anything else — offline, a 5xx, a dropped connection — used to log the
    // user out too, which threw a signed-in user to /login every time the
    // backend hiccupped. Keep the cached user and retry once we are back
    // online or on the next interaction.
    let retryArmed = false;
    const retryEvents = ['online', 'focus', 'pointerdown', 'keydown'] as const;
    let retry: () => void = () => {};
    const disarmRetry = () => {
      if (!retryArmed) return;
      retryArmed = false;
      for (const ev of retryEvents) window.removeEventListener(ev, retry);
    };
    const armRetry = () => {
      if (retryArmed) return;
      retryArmed = true;
      for (const ev of retryEvents) window.addEventListener(ev, retry);
    };
    const hydrate = () =>
      authApi.me()
        .then(u => {
          disarmRetry();
          setUser(u);
          localStorage.setItem('t75_user', JSON.stringify(u));
        })
        .catch((err: unknown) => {
          const status = (err as { response?: { status?: number } })?.response?.status;
          if (status === 401) {
            disarmRetry();
            // Soft-land on login — do not leave a half-auth shell that
            // flashes "Something went wrong" over a dead session.
            // 403 is permission, not expiry — leave that to the caller.
            redirectToLogin();
            setToken(null);
            setUser(null);
            return;
          }
          if (!getAccessToken()) {
            // The interceptor already tore the session down (refresh 4xx).
            disarmRetry();
            return;
          }
          armRetry();
        })
        .finally(() => setLoading(false));
    retry = () => {
      disarmRetry();
      void hydrate();
    };
    void hydrate();

    return () => {
      window.removeEventListener('integral:logout', onLogout);
      disarmRetry();
    };
    // queryClient from useQueryClient is a stable reference — empty
    // dep array is correct.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login(email, password);
    setTokens(res);
    setToken(res.access_token);
    const u = await authApi.me();
    setUser(u); localStorage.setItem('t75_user', JSON.stringify(u));
  }, []);

  const signup = useCallback(
    async (
      email: string,
      password: string,
      display_name: string,
      workspaceName?: string
    ) => {
      const res = await authApi.signup(
        email,
        password,
        display_name,
        workspaceName
      );
      if (!res.access_token) throw new Error('Signup did not return a token');
      setTokens(res);
      setToken(res.access_token);
      const u = await authApi.me();
      setUser(u);
      localStorage.setItem('t75_user', JSON.stringify(u));
    },
    []
  );

  const logout = useCallback(async () => {
    try {
      if (getAccessToken()) {
        await authApi.logout();
      }
    } catch {
      /* still clear local session if the network fails */
    } finally {
      clearTokens();
      setToken(null);
      setUser(null);
      // Drop every cached query so the next signed-in user can't
      // inherit the previous user's workspaces / active scope /
      // notifications / etc. Pairs with the localStorage wipe in
      // ``clearTokens`` — together they leave a clean per-user slate.
      queryClient.clear();
      // Chat stream state lives in a module-level store so it survives
      // navigation; that means it also survives logout unless dropped here.
      // Aborts any in-flight turn — the one place where killing a turn is
      // right, since its owner is leaving.
      resetThreadSessions();
    }
  }, [queryClient]);

  const refreshUser = useCallback(async () => {
    const u = await authApi.me();
    setUser(u); localStorage.setItem('t75_user', JSON.stringify(u));
  }, []);

  // Memoized because this is the root-most context in the app: every render of
  // AuthProvider used to mint a fresh object literal, which invalidates all 36
  // useAuth() consumers even when nothing about the session changed.
  //
  // The knock-on effect was worse than the re-renders. AgentiveContext derives
  // its `refresh` callback from `user`, and its polling effect depends on that
  // callback — so a new context value tore down and re-established the
  // /agentive/status interval, firing an extra request off-cadence each time.
  //
  // Safe to memoize: login/signup/logout/refreshUser are all useCallback-stable,
  // so the identity only changes when the session state genuinely does.
  const value = useMemo(
    () => ({ user, token, loading, login, signup, logout, refreshUser }),
    [user, token, loading, login, signup, logout, refreshUser],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error('useAuth outside AuthProvider');
  return c;
}

/** Same as ``useAuth`` but returns ``null`` when no provider is present. Use
 *  this for components that may render in isolation (e.g. Vitest unit tests)
 *  but still want to personalize when an AuthProvider is mounted. */
export function useAuthOptional(): AuthCtx | null {
  return useContext(Ctx);
}

// Silence unused-import linter when refreshAccessToken is referenced
// indirectly via the axios interceptor — keep it imported so the
// module loads alongside AuthContext (ensures session.ts initializes
// before any other consumer touches it).
void refreshAccessToken;
