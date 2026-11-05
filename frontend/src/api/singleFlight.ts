/**
 * Share one in-flight (or very recent) request between callers.
 *
 * The SPA mounts several components that each ask for the same listing
 * (`/tracks`, `/apps`) during one page load. Each call costs the server a full
 * permission walk, so identical concurrent calls are collapsed into one.
 * Entries expire after `ttlMs` and are dropped on any successful write
 * (see `clearAllSingleFlights`, called from the axios response interceptor),
 * so a create/delete is never hidden behind a cached listing.
 */
type Entry<T> = { promise: Promise<T>; expires: number };

const registry = new Set<Map<string, Entry<unknown>>>();

export function createSingleFlight<T>(ttlMs: number) {
  const store = new Map<string, Entry<T>>();
  registry.add(store as Map<string, Entry<unknown>>);
  return {
    run(key: string, fn: () => Promise<T>): Promise<T> {
      const now = Date.now();
      const hit = store.get(key);
      if (hit && hit.expires > now) return hit.promise;
      const promise = fn().catch(err => {
        if (store.get(key)?.promise === promise) store.delete(key);
        throw err;
      });
      store.set(key, { promise, expires: now + ttlMs });
      return promise;
    },
    clear() {
      store.clear();
    },
  };
}

export function clearAllSingleFlights(): void {
  registry.forEach(s => s.clear());
}
