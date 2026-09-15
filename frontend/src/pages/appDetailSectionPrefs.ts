/** Last Tracks / Dashboards tab per app — localStorage, device-scoped. */

export type AppDetailSection = 'tracks' | 'dashboards';

const STORAGE_PREFIX = 'integral:app-section:';

function storageKey(appId: string): string {
  return `${STORAGE_PREFIX}${appId}`;
}

export function readAppDetailSection(appId: string): AppDetailSection {
  try {
    const raw = localStorage.getItem(storageKey(appId));
    if (raw === 'tracks' || raw === 'dashboards') return raw;
  } catch {
    /* private mode / quota */
  }
  return 'tracks';
}

export function writeAppDetailSection(
  appId: string,
  section: AppDetailSection
): void {
  try {
    localStorage.setItem(storageKey(appId), section);
  } catch {
    /* private mode / quota */
  }
}
