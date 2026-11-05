import axios, { type AxiosInstance, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';

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

/**
 * Share one network call between identical GETs that are in flight at the same
 * moment (same URL, params, auth and workspace scope).
 *
 * A page load mounts many components that ask for the same resource; under React
 * StrictMode in dev every mount effect also runs twice. Each duplicate costs the
 * server a full permission walk. Only overlapping requests are shared: the entry is
 * dropped the moment the call settles, so nothing is ever served stale.
 * Requests with an abort signal, progress callbacks or a non-JSON response type are
 * left alone, and each caller gets its own copy of the response data.
 */
export function dedupeConcurrentGets(client: AxiosInstance): void {
  const base = axios.getAdapter(client.defaults.adapter);
  const inflight = new Map<string, Promise<AxiosResponse>>();
  client.defaults.adapter = (config: InternalAxiosRequestConfig) => {
    const method = String(config.method || 'get').toLowerCase();
    if (
      method !== 'get' ||
      config.signal ||
      config.onDownloadProgress ||
      (config.responseType && config.responseType !== 'json')
    ) {
      return base(config);
    }
    const headers = config.headers as unknown as { get?: (n: string) => unknown } | undefined;
    const key = JSON.stringify([
      axios.getUri(config),
      headers?.get?.('Authorization') ?? '',
      headers?.get?.('X-Integral-Scope') ?? '',
    ]);
    const shared = inflight.get(key);
    if (shared) {
      return shared.then(response => ({
        ...response,
        config,
        data: cloneData(response.data),
      }));
    }
    const promise = base(config);
    inflight.set(key, promise);
    const clear = () => {
      if (inflight.get(key) === promise) inflight.delete(key);
    };
    promise.then(clear, clear);
    return promise;
  };
}

function cloneData<T>(data: T): T {
  if (data === null || typeof data !== 'object') return data;
  try {
    return structuredClone(data);
  } catch {
    return JSON.parse(JSON.stringify(data)) as T;
  }
}
