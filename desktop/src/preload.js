'use strict';

/**
 * Integral desktop — preload bridge.
 *
 * Runs in the isolated preload world with access to ipcRenderer; exposes a
 * minimal `window.integralDesktop` surface to the renderer. The web app
 * stays capability-free (no nodeIntegration) — the ONLY privileged fact it
 * receives is the backend origin. `frontend/src/config.ts` reads
 * `getApiUrl()` first, before same-origin `/api` and build-time env.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('integralDesktop', {
  /** Synchronous backend origin (e.g. `http://localhost:4000`), no path. */
  getApiUrl: () => {
    try {
      const url = ipcRenderer.sendSync('integral:get-api-url');
      return typeof url === 'string' && url ? url : null;
    } catch {
      return null;
    }
  },
  /** Persist a new backend origin in the shell's settings.json. The
   *  renderer must reload for REST/WS clients to pick it up. */
  setApiUrl: (url) => ipcRenderer.invoke('integral:set-api-url', url),
  /** True when running inside this Electron shell (vs. a browser tab). */
  isDesktop: true,
  platform: process.platform,
});
