import {
  contentProfileDraftsApi,
  type SubstrateResponse,
} from '../../api/contentProfileDrafts';

let _substrateCache: SubstrateResponse | null = null;
let _initPromise: Promise<SubstrateResponse | null> | null = null;

export async function initContentProfilePlugins(): Promise<SubstrateResponse | null> {
  if (_substrateCache) return _substrateCache;
  if (_initPromise) return _initPromise;
  _initPromise = (async () => {
    try {
      const sub = await contentProfileDraftsApi.substrate();
      _substrateCache = sub;
      return sub;
    } catch (err) {

      console.warn('content-profile substrate fetch failed', err);
      return null;
    } finally {
      _initPromise = null;
    }
  })();
  return _initPromise;
}

export function getDiscoveredPlugins(): SubstrateResponse | null {
  return _substrateCache;
}

export function _resetForTests(): void {
  _substrateCache = null;
  _initPromise = null;
}
