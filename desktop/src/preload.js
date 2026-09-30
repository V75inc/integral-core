'use strict';

/**
 * Integral desktop — preload bridge.
 *
 * Runs in the isolated preload world with access to ipcRenderer; exposes a
 * minimal `window.integralDesktop` surface to the renderer. The web app
 * stays capability-free (no nodeIntegration). The bridge exposes the backend
 * origin plus narrow, one-way desktop presentation channels for recent chats
 * and native notifications.
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
  /** Replace the macOS menu-bar conversation shortcuts. */
  setRecentConversations: (conversations) => {
    ipcRenderer.send('integral:set-recent-conversations', conversations);
  },
  /** Sync the canonical notification snapshot to the host notification center. */
  syncNativeNotifications: (snapshot) => {
    ipcRenderer.send('integral:sync-native-notifications', snapshot);
  },
  /** Fail-closed local environment host configuration. */
  getDesktopEnvironmentConfig: () =>
    ipcRenderer.sendSync('integral:get-desktop-environment-config'),
  /** Connect Electron main with a short-lived, backend-minted host ticket. */
  connectDesktopEnvironment: (session) =>
    ipcRenderer.invoke('integral:connect-desktop-environment', session),
  /** Current live binding selector; null until the backend accepts the host. */
  getDesktopEnvironmentBindingId: () =>
    ipcRenderer.sendSync('integral:get-desktop-environment-binding'),
  /**
   * Computer use (Cua Driver). Start/stop are explicit — nothing here starts a
   * desktop-automation process on page load, and `enabled` is a fail-closed
   * opt-in the user sets in the Environment menu.
   */
  getComputerUseConfig: () => ipcRenderer.sendSync('integral:get-computer-use-config'),
  /** Re-run local packaged-runtime readiness checks. */
  probeComputerUse: () => ipcRenderer.invoke('integral:probe-computer-use'),
  /** Native-consent-gated local application discovery for scope selection. */
  listComputerUseApps: () => ipcRenderer.invoke('integral:list-computer-use-apps'),
  /**
   * Request a locally scoped lease. Electron main always renders the native
   * disclosure/consent dialog; renderer input can propose scope but cannot
   * approve it.
   */
  approveComputerUse: (proposal) =>
    ipcRenderer.invoke('integral:approve-computer-use', proposal),
  stopComputerUse: () => ipcRenderer.invoke('integral:stop-computer-use'),
  /**
   * Enable TypeSafe Jev and store the API key in the OS keychain. The key never
   * returns to the renderer after save.
   */
  setComputerUseJev: (patch) => ipcRenderer.invoke('integral:set-computer-use-jev', patch),
  onDesktopEnvironmentDisconnected: (callback) => {
    const listener = () => callback();
    ipcRenderer.on('integral:desktop-environment-disconnected', listener);
    return () =>
      ipcRenderer.removeListener('integral:desktop-environment-disconnected', listener);
  },
  /** True when running inside this Electron shell (vs. a browser tab). */
  isDesktop: true,
  platform: process.platform,
});
