'use strict';

/**
 * Integral desktop — Electron main process.
 *
 * Two modes:
 *   dev   (`npm run dev`, or `--dev`): loads the Vite dev server so the
 *           desktop shell hot-reloads against `../frontend` (which must be
 *           running: `npm run dev --prefix ../frontend`). The dev server's
 *           own proxy forwards `/api` + `/ws` to the backend.
 *   prod  (`npm start` after `npm run build:renderer`, or a packaged build):
 *           loads the bundled renderer (`renderer/index.html`, copied from
 *           `../frontend/dist`). REST + WebSocket calls go straight at the
 *           configured backend origin — see resolveApiUrl().
 *
 * Backend URL resolution (first non-empty wins):
 *   1. INTEGRAL_API_URL env var / --api-url=<url> CLI flag
 *   2. settings.json in the app userData dir ({ "apiUrl": "..." })
 *   3. http://localhost:4000
 *
 * The renderer reads the value synchronously through the preload bridge
 * (`window.integralDesktop.getApiUrl()`), which `frontend/src/config.ts`
 * prefers over same-origin `/api` (`file://` has no meaningful origin).
 */

const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { DesktopEnvironmentHost } = require('./environment-host');
const { ComputerUseBroker } = require('./computer-use-broker');
const {
  grantFromMemory,
  grantMemoryPath,
  isLiveGrantMemory,
  publicGrantConfig,
  readGrantMemory,
  rememberApprovedGrant,
  rememberExpiredGrant,
  rememberRevokedGrant,
} = require('./computer-use-grant-store');
const {
  applyJevSettingsPatch,
  jevSettingsPath,
  loadJevRuntimeSettings,
  publicJevConfig,
  readJevSettingsRecord,
} = require('./jev-settings');
const { chooseWithTypesafe } = require('./jev-choose');
const { bundledDriverPath: resolveBundledDriverPath } = require('./driver-resource');
const {
  app,
  BrowserWindow,
  Menu,
  Notification,
  Tray,
  dialog,
  ipcMain,
  nativeImage,
  shell,
  systemPreferences,
  desktopCapturer,
  safeStorage,
} = require('electron');

const DEFAULT_API_URL = 'http://localhost:4000';
const DEV_URL = process.env.INTEGRAL_DESKTOP_DEV_URL || 'http://localhost:9006';

const isDev = process.argv.includes('--dev') || process.env.INTEGRAL_DESKTOP_DEV === '1';

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
}

function readSettings() {
  try {
    const raw = fs.readFileSync(settingsPath(), 'utf8');
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') return parsed;
  } catch {
    // Missing or corrupt — fall through to defaults.
  }
  return {};
}

function writeSettings(patch) {
  const next = { ...readSettings(), ...patch };
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2));
}

function cliFlag(name) {
  const prefix = `--${name}=`;
  const arg = process.argv.find((a) => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : null;
}

function resolveApiUrl() {
  const fromEnv = (process.env.INTEGRAL_API_URL || '').trim();
  const fromFlag = (cliFlag('api-url') || '').trim();
  const fromSettings = String(readSettings().apiUrl || '').trim();
  const raw = fromFlag || fromEnv || fromSettings || DEFAULT_API_URL;
  return raw.replace(/\/+$/, '');
}

let apiUrl = DEFAULT_API_URL;
let mainWindow = null;
let quickAccessTray = null;
let recentConversations = [];
let unreadNotificationCount = 0;
let nativeNotificationSeenIds = null;
const activeNativeNotifications = new Set();
let desktopEnvironmentHost = null;
let computerUseBroker = null;
let activeComputerUseGrant = null;

function bundledDriverPath() {
  return resolveBundledDriverPath({
    resourcesPath: process.resourcesPath,
    appRoot: path.join(__dirname, '..'),
    packaged: app.isPackaged,
    platform: process.platform,
  });
}

function normalizeConsentApps(rawApps) {
  if (!Array.isArray(rawApps) || rawApps.length === 0 || rawApps.length > 10) {
    throw new Error('Choose between one and ten applications');
  }
  return rawApps.map((raw) => {
    const pid = Number(raw?.pid);
    if (!Number.isSafeInteger(pid) || pid <= 0) {
      throw new Error('Every application needs its current process identifier');
    }
    const name = String(raw?.name || '').trim().slice(0, 120);
    if (process.platform === 'darwin') {
      const bundleId = String(raw?.bundleId || '').trim();
      if (!/^[A-Za-z0-9.-]{3,200}$/.test(bundleId)) {
        throw new Error('Every macOS application needs a valid bundle identifier');
      }
      return { pid, name: name || bundleId, bundleId };
    }
    const executable = String(raw?.executable || '').trim();
    if (!path.isAbsolute(executable)) {
      throw new Error('Every application needs an absolute executable path');
    }
    return { pid, name: name || path.basename(executable), executable };
  });
}

async function requestHostMacOSPermissions() {
  const accessibilityTrusted = systemPreferences.isTrustedAccessibilityClient(true);
  let captured = false;
  try {
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: 1, height: 1 },
    });
    captured = Array.isArray(sources);
  } catch {
    captured = false;
  }
  let screenStatus = 'unknown';
  try {
    screenStatus = systemPreferences.getMediaAccessStatus('screen');
  } catch {
    screenStatus = 'unknown';
  }
  return {
    accessibilityTrusted: accessibilityTrusted === true,
    screenGranted: captured || screenStatus === 'granted',
    screenStatus,
  };
}

async function ensureMacOSComputerUsePermissions() {
  if (process.platform !== 'darwin') return true;
  const host = await requestHostMacOSPermissions();
  try {
    const { requestMacOSPermissions } = await import('@trycua/cua-driver/electron');
    requestMacOSPermissions();
  } catch {
    // Best-effort; a failed Cua probe must not override the Settings toggles.
  }
  if (!host.accessibilityTrusted || !host.screenGranted) {
    // Unsigned `npm start` / Electron.app routinely reports false here
    // after the user has already enabled Electron in Accessibility and
    // Screen Recording. Do not fail closed on that API. Start the worker;
    // a real TCC miss surfaces as a Cua snapshot/action error.
    console.warn('[computer-use] Electron TCC APIs still report missing grants', host);
  }
  return true;
}

function backendAllowsSensitiveEgress(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return (
      url.protocol === 'https:' ||
      ['localhost', '127.0.0.1', '::1'].includes(url.hostname)
    );
  } catch {
    return false;
  }
}

async function approveComputerUse(proposal) {
  const approvedBinding = desktopEnvironmentHost?.bindingId;
  const approvedApiUrl = apiUrl;
  if (!approvedBinding) {
    return { ok: false, code: 'computer_use.desktop_not_connected' };
  }
  if (!backendAllowsSensitiveEgress(approvedApiUrl)) {
    return {
      ok: false,
      code: 'computer_use.secure_backend_required',
      message: 'Computer use requires HTTPS for a non-local backend.',
    };
  }
  const apps = normalizeConsentApps(proposal?.apps);
  const durationMinutes = Math.min(
    480,
    Math.max(5, Number(proposal?.durationMinutes) || 30),
  );
  const screenshotsAllowed = proposal?.screenshotsAllowed === true;
  const actionsAllowed = proposal?.actionsAllowed === true;
  const skipConsentDialog =
    (isDev || process.env.INTEGRAL_DESKTOP_SKIP_CUA_CONSENT === '1') &&
    backendAllowsSensitiveEgress(approvedApiUrl);
  const detail = [
    `Backend: ${approvedApiUrl}`,
    `Duration: ${durationMinutes} minutes`,
    '',
    'Applications:',
    ...apps.flatMap((item) => [
      `• ${item.name}`,
      `  ${item.bundleId || item.executable}`,
    ]),
    '',
    screenshotsAllowed
      ? 'Window screenshots and accessibility text may leave this device and be sent to the configured Integral backend/model.'
      : 'Only application and window metadata may leave this device; screenshots are not allowed.',
    '',
    actionsAllowed
      ? 'Background clicks, typing, keypresses, and hotkeys may run against the selected applications without bringing them to the front. Foreground control, launching, and terminating remain disabled.'
      : 'Input is not enabled. Integral may observe approved windows only.',
    '',
    loadJevRuntimeSettings(computerUseJevPath(), { decrypt: decryptJevSecret }).enabled
      ? 'Jev is enabled. Compact accessibility labels and candidate IDs may be sent to TypeSafe (api.typesafe.ai) to choose the next action. Screenshots and element tokens are not sent to TypeSafe.'
      : 'Jev is off. The resident chooses actions without TypeSafe.',
    '',
    'Full-display capture, browser automation, and foreground control are not enabled in this phase.',
  ].join('\n');
  if (!skipConsentDialog) {
    const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
      type: 'warning',
      title: actionsAllowed
        ? 'Allow Integral to observe and control these applications?'
        : 'Allow Integral to observe these applications?',
      message: 'Approve temporary computer-use access',
      detail,
      buttons: ['Allow', 'Cancel'],
      defaultId: 1,
      cancelId: 1,
    });
    if (response !== 0) return { ok: false, code: 'computer_use.consent_declined' };
  }
  if (!(await ensureMacOSComputerUsePermissions())) {
    return { ok: false, code: 'computer_use.os_permissions_required' };
  }
  if (
    desktopEnvironmentHost?.bindingId !== approvedBinding ||
    apiUrl !== approvedApiUrl
  ) {
    return {
      ok: false,
      code: 'computer_use.binding_changed_during_consent',
      message: 'The backend connection changed. Review and approve the new connection.',
    };
  }

  const now = new Date();
  const grant = {
    id: crypto.randomUUID(),
    principalId: String(proposal?.principalId || ''),
    workspaceId: String(proposal?.workspaceId || ''),
    bindingGeneration: approvedBinding,
    approvedLocally: true,
    approvedAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + durationMinutes * 60_000).toISOString(),
    idleTimeoutSeconds: Math.min(1800, durationMinutes * 60),
    screenshotsAllowed,
    actionsAllowed,
    apps,
  };
  const activation = await computerUseBroker.activate(grant);
  activeComputerUseGrant = grant;
  rememberApprovedGrant(computerUseGrantMemoryPath(), grant, durationMinutes);
  desktopEnvironmentHost.publishHostManifest();
  buildMenu();
  return {
    ok: true,
    grantId: grant.id,
    expiresAt: grant.expiresAt,
    runtimeGeneration: activation.runtimeGeneration,
    durationMinutes,
    apps: grant.apps.map((app) => ({
      name: app.name,
      ...(app.bundleId ? { bundleId: app.bundleId } : {}),
      ...(app.executable ? { executable: app.executable } : {}),
    })),
    screenshotsAllowed,
    actionsAllowed,
  };
}

function computerUseGrantMemoryPath() {
  return grantMemoryPath(app.getPath('userData'));
}

function computerUsePublicConfig() {
  return {
    bundled: fs.existsSync(bundledDriverPath()),
    active: Boolean(activeComputerUseGrant),
    bindingGeneration: desktopEnvironmentHost?.bindingId ?? null,
    ...publicGrantConfig(
      readGrantMemory(computerUseGrantMemoryPath()),
      activeComputerUseGrant,
    ),
    ...publicJevConfig(readJevSettingsRecord(computerUseJevPath())),
  };
}

function computerUseJevPath() {
  return jevSettingsPath(app.getPath('userData'));
}

function encryptJevSecret(value) {
  if (!safeStorage.isEncryptionAvailable()) {
    const error = new Error(
      'This device cannot store a TypeSafe API key in the OS keychain',
    );
    error.code = 'computer_use.jev_encryption_unavailable';
    throw error;
  }
  return safeStorage.encryptString(value).toString('base64');
}

function decryptJevSecret(cipher) {
  if (!cipher) return '';
  return safeStorage.decryptString(Buffer.from(String(cipher), 'base64'));
}

function saveComputerUseJev(patch) {
  const saved = applyJevSettingsPatch(computerUseJevPath(), patch, {
    encrypt: encryptJevSecret,
    decrypt: decryptJevSecret,
  });
  desktopEnvironmentHost?.publishHostManifest();
  return {
    ok: true,
    jevEnabled: saved.jevEnabled,
    jevConfigured: saved.jevConfigured,
  };
}

function rememberedComputerUseMenuLabel() {
  const memory = readGrantMemory(computerUseGrantMemoryPath());
  if (!memory?.apps?.length) return 'Computer Use: no remembered local approval';
  const names = memory.apps.map((app) => app.name).join(', ');
  if (isLiveGrantMemory(memory)) {
    return `Computer Use: ${names} remembered until ${new Date(memory.expiresAt).toLocaleTimeString()}`;
  }
  return `Computer Use: last approved ${names} for ${memory.durationMinutes} min`;
}

async function restoreComputerUseIfNeeded() {
  if (activeComputerUseGrant || !computerUseBroker) return false;
  const bindingGeneration = desktopEnvironmentHost?.bindingId;
  if (!bindingGeneration || !backendAllowsSensitiveEgress(apiUrl)) return false;
  const memory = readGrantMemory(computerUseGrantMemoryPath());
  const grant = grantFromMemory(memory, bindingGeneration);
  if (!grant) return false;
  if (!(await ensureMacOSComputerUsePermissions())) return false;
  try {
    await computerUseBroker.activate(grant);
    activeComputerUseGrant = grant;
    desktopEnvironmentHost.publishHostManifest();
    buildMenu();
    return true;
  } catch (error) {
    console.warn('[computer-use] could not restore the remembered grant:', error.message);
    return false;
  }
}

async function stopComputerUse({ revoke = true } = {}) {
  activeComputerUseGrant = null;
  await computerUseBroker?.stop();
  if (revoke) rememberRevokedGrant(computerUseGrantMemoryPath());
  desktopEnvironmentHost?.publishHostManifest();
  buildMenu();
  return { ok: true };
}

function insideRoundedRect(x, y, left, top, right, bottom, radius) {
  const nearestX = Math.max(left + radius, Math.min(x, right - radius));
  const nearestY = Math.max(top + radius, Math.min(y, bottom - radius));
  return (x - nearestX) ** 2 + (y - nearestY) ** 2 <= radius ** 2;
}

/**
 * Build a Retina-ready monochrome version of Integral's hollow-square mark.
 * Keeping the pixels in code avoids a second tray-only brand asset drifting
 * from the renderer mark. macOS recolors template images for either menu-bar
 * appearance.
 */
function createQuickAccessIcon() {
  const scaleFactor = 2;
  const logicalSize = 18;
  const size = logicalSize * scaleFactor;
  const pixels = Buffer.alloc(size * size * 4);
  const samplesPerAxis = 4;
  const sampleCount = samplesPerAxis ** 2;

  for (let py = 0; py < size; py += 1) {
    for (let px = 0; px < size; px += 1) {
      let covered = 0;
      for (let sy = 0; sy < samplesPerAxis; sy += 1) {
        for (let sx = 0; sx < samplesPerAxis; sx += 1) {
          const x = px + (sx + 0.5) / samplesPerAxis;
          const y = py + (sy + 0.5) / samplesPerAxis;
          const inOuter = insideRoundedRect(x, y, 2, 2, size - 2, size - 2, 8);
          const inCutout = insideRoundedRect(x, y, 10, 10, size - 10, size - 10, 4);
          if (inOuter && !inCutout) covered += 1;
        }
      }

      const offset = (py * size + px) * 4;
      // createFromBitmap consumes BGRA. The RGB channels stay black because
      // template rendering uses alpha as the mask.
      pixels[offset + 3] = Math.round((covered / sampleCount) * 255);
    }
  }

  const image = nativeImage.createFromBitmap(pixels, {
    width: size,
    height: size,
    scaleFactor,
  });
  image.setTemplateImage(true);
  return image;
}

function rendererEntry() {
  if (isDev) return null;
  // Packaged build: electron-builder packs `renderer/` next to `src/`.
  // Unpackaged `npm start`: the copy script wrote `renderer/` beside `src/`.
  const packaged = path.join(process.resourcesPath, 'app', 'renderer', 'index.html');
  if (fs.existsSync(packaged)) return packaged;
  return path.join(__dirname, '..', 'renderer', 'index.html');
}

function createWindow() {
  // ChatGPT-style minimal chrome: on macOS the native title bar is hidden
  // and the traffic lights float over the content (positioned to clear the
  // sidebar's logo row — see Sidebar's shell clearance). The renderer
  // provides the drag surface (`-webkit-app-region: drag` on the sidebar /
  // auth layouts); without it a hidden-titlebar window can't be moved.
  // Windows/Linux keep the native frame (auto-hidden menu); only macOS
  // gets the overlay look for now.
  const hiddenTitlebar = process.platform === 'darwin';
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 600,
    title: 'Integral',
    backgroundColor: '#0b0e14',
    autoHideMenuBar: true,
    ...(hiddenTitlebar
      ? { titleBarStyle: 'hidden', trafficLightPosition: { x: 16, y: 16 } }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  if (isDev) {
    mainWindow.loadURL(DEV_URL);
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    const entry = rendererEntry();
    if (!fs.existsSync(entry)) {
      dialog.showErrorBox(
        'Renderer missing',
        `No bundled frontend found.\n\nRun "npm run build:renderer" first, or start dev mode with "npm run dev" (needs the Vite server on ${DEV_URL}).`,
      );
      app.quit();
      return;
    }
    mainWindow.loadFile(entry);
  }

  // In dev the renderer is the Vite server, which may not be up yet when
  // the shell launches (ERR_CONNECTION_REFUSED). Offer a retry instead of
  // stranding the user on Electron's blank "unreachable" page.
  let retryDialogOpen = false;
  mainWindow.webContents.on('did-fail-load', (_event, code, desc, url, isMainFrame) => {
    if (!isMainFrame || !isDev || retryDialogOpen || !mainWindow) return;
    retryDialogOpen = true;
    void dialog
      .showMessageBox(mainWindow, {
        type: 'error',
        title: 'Frontend dev server unreachable',
        message: `Could not load ${url}`,
        detail:
          `The Vite dev server is not responding (${desc || code}).\n\n` +
          `Start it first:\n  npm run dev --prefix ../frontend\n\n` +
          `Then retry. (INTEGRAL_DESKTOP_DEV_URL overrides the expected URL.)`,
        buttons: ['Retry', 'Quit'],
        defaultId: 0,
        cancelId: 1,
      })
      .then(({ response }) => {
        retryDialogOpen = false;
        if (!mainWindow) return;
        if (response === 0) mainWindow.loadURL(DEV_URL);
        else app.quit();
      });
  });
  // Keep navigation inside the app for app routes; pop real external links
  // out to the OS browser so the shell never traps the user.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const entry = isDev ? DEV_URL : 'file:';
    if (!url.startsWith(entry)) {
      event.preventDefault();
      void shell.openExternal(url);
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

async function openIntegral(route = null) {
  if (!mainWindow || mainWindow.isDestroyed()) createWindow();
  if (!mainWindow || mainWindow.isDestroyed()) return;

  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();

  if (!route) return;
  if (mainWindow.webContents.isLoadingMainFrame()) {
    await new Promise((resolve) => {
      mainWindow.webContents.once('did-finish-load', resolve);
    });
  }
  if (!mainWindow || mainWindow.isDestroyed()) return;

  const serializedRoute = JSON.stringify(route);
  await mainWindow.webContents.executeJavaScript(`
    (() => {
      const route = ${serializedRoute};
      if (window.location.protocol === 'file:') {
        window.location.hash = '#' + route;
        return;
      }
      window.history.pushState({}, '', route);
      window.dispatchEvent(new PopStateEvent('popstate'));
    })()
  `);
}

function openFromQuickAccess(route = null) {
  void openIntegral(route).catch((error) => {
    console.error('Could not open Integral from the quick-access menu:', error);
  });
}

function buildQuickAccessMenu() {
  const recentItems =
    recentConversations.length > 0
      ? recentConversations.map((conversation) => ({
          label: conversation.title,
          type: 'radio',
          checked: conversation.active,
          click: () =>
            openFromQuickAccess(`/agent?thread=${encodeURIComponent(conversation.id)}`),
        }))
      : [{ label: 'No recent conversations', enabled: false }];

  return Menu.buildFromTemplate([
    {
      label: 'Open Integral',
      click: () => openFromQuickAccess(),
    },
    { type: 'separator' },
    { label: 'Recent Conversations', enabled: false },
    ...recentItems,
    { type: 'separator' },
    {
      label: 'New Chat',
      accelerator: 'CommandOrControl+N',
      click: () => openFromQuickAccess(`/agent?new=${Date.now()}`),
    },
    {
      label:
        unreadNotificationCount > 0
          ? `Notifications (${unreadNotificationCount})`
          : 'Notifications',
      click: () => openFromQuickAccess('/notifications'),
    },
    { type: 'separator' },
    {
      label: 'Settings',
      click: () => openFromQuickAccess('/settings'),
    },
    {
      label: 'Quit Integral',
      accelerator: 'Command+Q',
      click: () => app.quit(),
    },
  ]);
}

function refreshQuickAccessMenu() {
  if (quickAccessTray) quickAccessTray.setContextMenu(buildQuickAccessMenu());
}

function installQuickAccessMenu() {
  if (process.platform !== 'darwin' || quickAccessTray) return;
  quickAccessTray = new Tray(createQuickAccessIcon());
  quickAccessTray.setToolTip('Integral quick access');
  refreshQuickAccessMenu();
}

function syncRecentConversations(value) {
  if (!Array.isArray(value)) return;
  recentConversations = value.slice(0, 10).flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const id = String(item.id || '').trim().slice(0, 200);
    const title = String(item.title || '').trim().slice(0, 80);
    if (!id || !title) return [];
    return [{ id, title, active: item.active === true }];
  });
  refreshQuickAccessMenu();
}

function syncNativeNotifications(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.notifications)) return;
  const notifications = value.notifications.slice(0, 100).flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const id = String(item.id || '').trim().slice(0, 240);
    const body = String(item.body || '').trim().slice(0, 500);
    const rawRoute = String(item.route || '').trim();
    const route =
      rawRoute.startsWith('/') && !rawRoute.startsWith('//') && rawRoute.length <= 500
        ? rawRoute
        : '/notifications';
    if (!id || !body) return [];
    return [{ id, body, route, unread: item.unread === true }];
  });

  unreadNotificationCount = Math.max(
    0,
    Math.min(999, Number(value.unreadCount) || 0),
  );
  if (process.platform === 'darwin' && app.dock) {
    app.dock.setBadge(unreadNotificationCount > 0 ? String(unreadNotificationCount) : '');
  }
  refreshQuickAccessMenu();

  if (nativeNotificationSeenIds === null) {
    const savedSeenIds = readSettings().nativeNotificationSeenIds;
    if (Array.isArray(savedSeenIds)) {
      nativeNotificationSeenIds = new Set(savedSeenIds.map(String));
    } else {
      nativeNotificationSeenIds = new Set(notifications.map((item) => item.id));
      // First sync establishes a baseline so enabling native notifications
      // does not replay the user's entire unread history.
      try {
        writeSettings({
          nativeNotificationSeenIds: Array.from(nativeNotificationSeenIds).slice(-500),
        });
      } catch (error) {
        console.warn('Could not persist native notification baseline:', error);
      }
      return;
    }
  }

  const fresh = notifications.filter(
    (item) => item.unread && !nativeNotificationSeenIds.has(item.id),
  );
  for (const item of notifications) nativeNotificationSeenIds.add(item.id);
  const retainedSeenIds = Array.from(nativeNotificationSeenIds).slice(-500);
  nativeNotificationSeenIds = new Set(retainedSeenIds);
  try {
    writeSettings({ nativeNotificationSeenIds: retainedSeenIds });
  } catch (error) {
    console.warn('Could not persist native notification state:', error);
  }

  if (!Notification.isSupported()) return;
  for (const item of fresh.reverse()) {
    const notification = new Notification({
      title: 'Integral',
      body: item.body,
    });
    notification.on('click', () => {
      activeNativeNotifications.delete(notification);
      openFromQuickAccess(item.route);
    });
    notification.on('close', () => activeNativeNotifications.delete(notification));
    activeNativeNotifications.add(notification);
    notification.show();
  }
}

function buildMenu() {
  const template = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Backend',
      submenu: [
        {
          label: `Current: ${apiUrl}`,
          enabled: false,
        },
        {
          label: 'Change Backend URL…',
          click: async () => {
            // Basic prompt-free flow: show where the value comes from and
            // let the user pick a preset or keep the current one. Free-form
            // entry lives in settings.json / INTEGRAL_API_URL (see README).
            const { response } = await dialog.showMessageBox(mainWindow, {
              type: 'question',
              title: 'Backend URL',
              message: `Backend: ${apiUrl}`,
              detail:
                'Set INTEGRAL_API_URL, pass --api-url=<url>, or edit settings.json ' +
                `at ${settingsPath()} (then reload).`,
              buttons: ['Reload', 'Open settings folder', 'Cancel'],
              defaultId: 2,
              cancelId: 2,
            });
            if (response === 0 && mainWindow) {
              apiUrl = resolveApiUrl();
              mainWindow.reload();
            } else if (response === 1) {
              await shell.showItemInFolder(settingsPath());
            }
          },
        },
      ],
    },
    {
      label: 'Environment',
      submenu: [
        {
          label: desktopEnvironmentHost?.bindingId
            ? 'Status: Connected'
            : 'Status: Not connected',
          enabled: false,
        },
        { type: 'separator' },
        {
          label: 'Enable local environment access',
          type: 'checkbox',
          checked: desktopEnvironmentHost?.config().enabled === true,
          click: (item) => {
            desktopEnvironmentHost?.setEnabled(item.checked);
            if (mainWindow) mainWindow.reload();
          },
        },
        {
          label: 'Grant Folder…',
          click: async () => {
            if (!mainWindow || !desktopEnvironmentHost) return;
            const result = await dialog.showOpenDialog(mainWindow, {
              properties: ['openDirectory'],
              title: 'Grant Integral read access to a folder',
            });
            if (!result.canceled && result.filePaths[0]) {
              desktopEnvironmentHost.addRoot(result.filePaths[0]);
            }
          },
        },
        { type: 'separator' },
        {
          label: activeComputerUseGrant
            ? `Computer Use: ${activeComputerUseGrant.apps.map((app) => app.name).join(', ')} until ${new Date(activeComputerUseGrant.expiresAt).toLocaleTimeString()}`
            : rememberedComputerUseMenuLabel(),
          enabled: false,
        },
        {
          label: 'Revoke Computer Use',
          enabled: Boolean(activeComputerUseGrant),
          click: async () => {
            await stopComputerUse();
          },
        },
        {
          label: 'About Computer Use Access…',
          click: async () => {
            await dialog.showMessageBox(mainWindow ?? undefined, {
              type: 'info',
              title: 'Integral Computer Use',
              message: 'Computer use is built into Integral Desktop',
              detail: [
                'No separate Cua Driver installation is required.',
                '',
                'Access starts only after a native approval dialog names the backend, applications, data leaving this device, and duration.',
                'Use Integral Settings to request access. This menu can revoke it immediately.',
              ].join('\n'),
              buttons: ['OK'],
            });
          },
        },
      ],
    },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  apiUrl = resolveApiUrl();
  computerUseBroker = new ComputerUseBroker({
    binaryPath: bundledDriverPath(),
    hostBundleId: 'ai.integral.desktop',
    stateDir: path.join(app.getPath('userData'), 'computer-use'),
    jev: {
      getSettings: () =>
        loadJevRuntimeSettings(computerUseJevPath(), { decrypt: decryptJevSecret }),
      choose: chooseWithTypesafe,
    },
    onRevoked: () => {
      activeComputerUseGrant = null;
      rememberExpiredGrant(computerUseGrantMemoryPath());
      desktopEnvironmentHost?.publishHostManifest();
      buildMenu();
    },
  });
  desktopEnvironmentHost = new DesktopEnvironmentHost({
    readSettings,
    writeSettings,
    getApiUrl: () => apiUrl,
    notifyReady: () => buildMenu(),
    notifyDisconnected: () => {
      void stopComputerUse({ revoke: false }).catch((error) => {
        console.warn('[computer-use] disconnect teardown failed:', error.message);
      });
      buildMenu();
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('integral:desktop-environment-disconnected');
      }
    },
    computerUseBroker,
  });
  buildMenu();
  installQuickAccessMenu();

  // Synchronous getter so the renderer's config module can resolve the
  // backend origin at import time (no async boot race).
  ipcMain.on('integral:get-api-url', (event) => {
    event.returnValue = apiUrl;
  });
  ipcMain.on('integral:set-recent-conversations', (_event, conversations) => {
    syncRecentConversations(conversations);
  });
  ipcMain.on('integral:sync-native-notifications', (_event, snapshot) => {
    syncNativeNotifications(snapshot);
  });
  ipcMain.handle('integral:set-api-url', async (_event, url) => {
    const next = String(url || '').trim().replace(/\/+$/, '');
    if (!next) throw new Error('Empty backend URL');
    apiUrl = next;
    try {
      writeSettings({ apiUrl: next });
    } catch (err) {
      throw new Error(`Could not persist backend URL: ${err.message}`);
    }
    return apiUrl;
  });
  ipcMain.on('integral:get-desktop-environment-config', (event) => {
    event.returnValue = desktopEnvironmentHost?.config() ?? { enabled: false };
  });
  ipcMain.on('integral:get-desktop-environment-binding', (event) => {
    event.returnValue = desktopEnvironmentHost?.bindingId ?? null;
  });
  ipcMain.handle('integral:connect-desktop-environment', async (_event, session) => {
    if (!desktopEnvironmentHost) return false;
    const connected = await desktopEnvironmentHost.connect(session);
    if (connected) await restoreComputerUseIfNeeded();
    return connected;
  });
  ipcMain.on('integral:get-computer-use-config', (event) => {
    event.returnValue = computerUsePublicConfig();
  });
  ipcMain.handle('integral:set-computer-use-jev', async (_event, patch) => {
    try {
      return saveComputerUseJev(patch || {});
    } catch (error) {
      return {
        ok: false,
        code: error.code ?? 'computer_use.jev_save_failed',
        message: error.message,
      };
    }
  });
  ipcMain.handle('integral:list-computer-use-apps', async () => {
    if (!desktopEnvironmentHost?.bindingId) {
      return { ok: false, code: 'computer_use.desktop_not_connected', apps: [] };
    }
    if (!fs.existsSync(bundledDriverPath())) {
      return { ok: false, code: 'computer_use.runtime_missing', apps: [] };
    }
    const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
      type: 'question',
      title: 'Choose applications for Computer Use',
      message: 'Allow Integral to list running applications?',
      detail: [
        `Backend: ${apiUrl}`,
        '',
        'The list is shown locally so you can choose an exact scope. No screenshots or accessibility content are captured by this step.',
      ].join('\n'),
      buttons: ['List Applications', 'Cancel'],
      defaultId: 1,
      cancelId: 1,
    });
    if (response !== 0) {
      return { ok: false, code: 'computer_use.consent_declined', apps: [] };
    }
    try {
      const discovered = await computerUseBroker.discoverApps();
      const apps = (discovered.apps || []).flatMap((item) => {
        if (!item?.running) return [];
        if (process.platform === 'darwin' && item.bundleId) {
          return [{ pid: item.pid, name: item.name, bundleId: item.bundleId }];
        }
        if (process.platform !== 'darwin' && item.launchPath && path.isAbsolute(item.launchPath)) {
          return [{ pid: item.pid, name: item.name, executable: item.launchPath }];
        }
        return [];
      });
      return { ok: true, apps };
    } catch (error) {
      return {
        ok: false,
        code: error.code ?? 'computer_use.discovery_failed',
        message: error.message,
        apps: [],
      };
    }
  });
  ipcMain.handle('integral:approve-computer-use', async (_event, proposal) => {
    try {
      if (!fs.existsSync(bundledDriverPath())) {
        return {
          ok: false,
          code: 'computer_use.runtime_missing',
          message: 'This Integral Desktop build does not contain its computer-use runtime',
        };
      }
      return await approveComputerUse(proposal);
    } catch (error) {
      console.warn('[computer-use] approval failed:', error.message);
      return {
        ok: false,
        code: error.code ?? 'computer_use.activation_failed',
        message: error.message,
      };
    }
  });
  ipcMain.handle('integral:probe-computer-use', async () => {
    return computerUsePublicConfig();
  });
  ipcMain.handle('integral:stop-computer-use', async () => {
    return stopComputerUse();
  });

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// A quit must never leave an orphaned process holding desktop-automation
// permissions. `before-quit` gives us a synchronous hook to fire; the
// graceful stop itself is awaited off the event loop.
let driverTeardownStarted = false;
app.on('before-quit', (event) => {
  if (!activeComputerUseGrant || driverTeardownStarted) return;
  event.preventDefault();
  driverTeardownStarted = true;
  void stopComputerUse({ revoke: false })
    .catch((error) => console.warn('[computer-use] teardown failed:', error.message))
    .finally(() => app.quit());
});
