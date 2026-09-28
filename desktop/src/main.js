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
const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  dialog,
  ipcMain,
  nativeImage,
  shell,
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
  return Menu.buildFromTemplate([
    {
      label: 'Open Integral',
      click: () => openFromQuickAccess(),
    },
    { type: 'separator' },
    {
      label: 'New Chat',
      accelerator: 'CommandOrControl+N',
      click: () => openFromQuickAccess(`/agent?new=${Date.now()}`),
    },
    {
      label: 'Notifications',
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

function installQuickAccessMenu() {
  if (process.platform !== 'darwin' || quickAccessTray) return;
  quickAccessTray = new Tray(createQuickAccessIcon());
  quickAccessTray.setToolTip('Integral quick access');
  quickAccessTray.setContextMenu(buildQuickAccessMenu());
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
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  apiUrl = resolveApiUrl();
  buildMenu();
  installQuickAccessMenu();

  // Synchronous getter so the renderer's config module can resolve the
  // backend origin at import time (no async boot race).
  ipcMain.on('integral:get-api-url', (event) => {
    event.returnValue = apiUrl;
  });
  ipcMain.handle('integral:set-api-url', async (_event, url) => {
    const next = String(url || '').trim().replace(/\/+$/, '');
    if (!next) throw new Error('Empty backend URL');
    apiUrl = next;
    try {
      fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
      fs.writeFileSync(settingsPath(), JSON.stringify({ apiUrl: next }, null, 2));
    } catch (err) {
      throw new Error(`Could not persist backend URL: ${err.message}`);
    }
    return apiUrl;
  });

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
