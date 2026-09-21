/**
 * CrumbsContext — single source of truth for the TopBar breadcrumb trail.
 *
 * Pages publish their trail with `useSetCrumbs([...])`. The TopBar reads
 * the latest trail from `useCrumbs()`. When a page doesn't publish (or
 * unmounts), the context falls back to a URL-derived best-guess crumb
 * so the chrome never goes empty.
 *
 * Why context over a per-page prop: pages live below `<Outlet />`, so
 * the Layout can't statically know each page's trail. The context lets
 * pages publish bottom-up while keeping render order clean.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import type { Crumb } from '../components/ui';

interface CrumbsContextValue {
  crumbs: Crumb[];
  setCrumbs: (next: Crumb[]) => void;
  clearCrumbs: () => void;
}

const CrumbsCtx = createContext<CrumbsContextValue | null>(null);

function toTitleCase(slug: string): string {
  return slug
    .replace(/-/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

/** Best-effort URL-based fallback when no page has published a trail.
 *  Note: Layout prepends a leading "Home" crumb on every page, so the
 *  fallback returns an empty list for "/" and only labels the section
 *  for deeper paths. */
function fallbackCrumbsFromPath(pathname: string): Crumb[] {
  const parts = pathname.split('/').filter(Boolean);
  if (parts.length === 0) return [];
  const top = parts[0];
  // For detail pages we deliberately *don't* include the raw id — the
  // page itself should publish a richer trail with the actual entity
  // name. The fallback only labels the section.
  const sectionLabel: Record<string, string> = {
    feed: 'Feed',
    tracks: 'Tracks',
    apps: 'Apps',
    workspaces: 'Workspaces',
    notifications: 'Notifications',
    profile: 'Profile',
    settings: 'Settings',
    'content-profiles': 'Operational Models',
    models: 'Operational Models',
    'agent': 'Agent',
  };
  return [{ label: sectionLabel[top] ?? toTitleCase(top) }];
}

export function CrumbsProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [published, setPublished] = useState<Crumb[] | null>(null);

  // Reset on navigation — each page is responsible for re-publishing its
  // crumbs after it mounts; without this, stale crumbs would briefly
  // flash from the previous page.
  const lastPathRef = useRef(location.pathname);
  useEffect(() => {
    if (lastPathRef.current !== location.pathname) {
      setPublished(null);
      lastPathRef.current = location.pathname;
    }
  }, [location.pathname]);

  const setCrumbs = useCallback((next: Crumb[]) => {
    setPublished(next);
  }, []);
  const clearCrumbs = useCallback(() => setPublished(null), []);

  const crumbs = useMemo(
    () => published ?? fallbackCrumbsFromPath(location.pathname),
    [published, location.pathname]
  );

  const value = useMemo<CrumbsContextValue>(
    () => ({ crumbs, setCrumbs, clearCrumbs }),
    [crumbs, setCrumbs, clearCrumbs]
  );

  return <CrumbsCtx.Provider value={value}>{children}</CrumbsCtx.Provider>;
}

export function useCrumbs(): CrumbsContextValue {
  const ctx = useContext(CrumbsCtx);
  if (!ctx) throw new Error('useCrumbs must be used within CrumbsProvider');
  return ctx;
}

/** Sugar hook — pages call `useSetCrumbs([...])` once, declaratively,
 *  and the context keeps the trail in sync until they unmount. */
export function useSetCrumbs(crumbs: Crumb[]): void {
  const { setCrumbs } = useCrumbs();
  // The dependency hash is the JSON form so callers can pass inline
  // arrays without memoizing. Cheap for the small lists we use here.
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    setCrumbs(crumbs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}
