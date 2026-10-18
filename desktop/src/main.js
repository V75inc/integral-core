'use strict';
const path = require('node:path');
const { app, BrowserWindow, Menu, dialog, shell, session, systemPreferences, nativeTheme, nativeImage } = require('electron');
const { startCore, runCli, validateWorkspace } = require('./core-runtime');
// Electron security baseline: https://www.electronjs.org/docs/latest/tutorial/security
const { createPermissions } = require('./permissions');
const { createThemeIcons } = require('./theme-icons');
const themeIcons = createThemeIcons({ app, nativeTheme, nativeImage, BrowserWindow });
let window = null;
let runtime = null;
let workspace = null;
let quitting = false;
let booting = true;
let permissions = null;
app.setName('Integral');

function flag(name) {
  return process.argv.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3);
}

function externalUrl(raw) {
  try {
    const url = new URL(raw);
    return ['https:', 'http:', 'mailto:'].includes(url.protocol) && !url.username && !url.password;
  } catch { return false; }
}

async function createWindow() {
  window = new BrowserWindow({
    width: 1280, height: 800, minWidth: 960, minHeight: 600,
    title: 'Integral', icon: themeIcons.current(), backgroundColor: '#ffffff', show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  window.once('ready-to-show', () => window?.show());
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (externalUrl(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    let sameOrigin = false;
    try { sameOrigin = new URL(url).origin === new URL(workspace).origin; } catch { /* blocked */ }
    if (!sameOrigin) {
      event.preventDefault();
      if (externalUrl(url)) void shell.openExternal(url);
    }
  });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.on('closed', () => { window = null; });
  await window.loadURL(workspace);
}

async function boot() {
  themeIcons.start();
  permissions = createPermissions({ session: session.defaultSession, systemPreferences, dialog, shell,
    consentPath: path.join(app.getPath('userData'), 'desktop-permissions.json'),
    getWindow: () => window, getWorkspace: () => workspace });
  const remote = flag('workspace-url') || process.env.INTEGRAL_WORKSPACE_URL;
  if (remote) workspace = validateWorkspace(remote);
  else {
    const resources = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', 'resources');
    const home = flag('home') || process.env.INTEGRAL_HOME || path.join(app.getPath('userData'), 'core');
    const progress = new BrowserWindow({ width: 560, height: 220, resizable: false,
      title: 'Starting Integral', icon: themeIcons.current(), webPreferences: { sandbox: true, nodeIntegration: false, contextIsolation: true } });
    await progress.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
      '<html><body style="font:16px system-ui;padding:30px"><h2>Starting Integral</h2><p>Preparing your private workspace. The first launch downloads its runtime; later launches reuse it.</p></body></html>'
    ));
    try { runtime = await startCore({ home, resources }); workspace = runtime.web_url; }
    finally { progress.close(); }
  }
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { label: 'File', submenu: [
      { label: 'Set Up AI Model…', click: () => { void window?.loadURL(new URL('/settings#ai-models', workspace).href); } },
      { label: 'Open in Browser', click: () => shell.openExternal(workspace) },
      { label: 'Microphone Access…', click: () => { void permissions.setup({ force: true }).catch(error => dialog.showErrorBox('Microphone setup needs attention', error.message)); } },
      ...(runtime ? [{ label: 'Open Installation Folder', click: () => shell.openPath(runtime.home) }] : []),
      { type: 'separator' }, { role: 'quit' },
    ] },
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
  ]));
  // A bundled Core upgrade may remove assets named by a previously cached shell.
  if (runtime) await session.defaultSession.clearCache();
  await createWindow();
  booting = false;
  await permissions.setup().catch(error => dialog.showErrorBox('Microphone setup needs attention', error.message));
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { window?.show(); window?.focus(); });
  app.whenReady().then(boot).catch(error => {
    dialog.showErrorBox('Integral could not start', error.message);
    app.quit();
  });
  app.on('activate', () => { if (!window && workspace) void createWindow(); });
  app.on('window-all-closed', () => { if (!booting && process.platform !== 'darwin') app.quit(); });
  app.on('before-quit', event => {
    if (!runtime?.owned || quitting) return;
    event.preventDefault(); quitting = true;
    void runCli(runtime.cli, ['stop', '--home', runtime.home, '--json',
      '--if-pid', String(runtime.pid), '--if-created', String(runtime.process_created)])
      .catch(error => dialog.showErrorBox('Integral shutdown needs attention', error.message))
      .finally(() => app.quit());
  });
}
