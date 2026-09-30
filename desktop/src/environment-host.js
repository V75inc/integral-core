'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const MAX_READ_BYTES = 256 * 1024;
const MAX_SCAN_FILE_BYTES = 1024 * 1024;
const MAX_LIST_ITEMS = 500;
const MAX_FIND_RESULTS = 200;
const MAX_GREP_RESULTS = 200;
const MAX_SCAN_ENTRIES = 5000;

function cleanLabel(value) {
  return String(value || '').trim().slice(0, 80);
}

function rootId(rootPath) {
  return crypto.createHash('sha256').update(rootPath).digest('hex').slice(0, 16);
}

function websocketUrl(apiUrl, websocketPath, ticket) {
  const base = String(apiUrl || '').replace(/^http/i, (match) =>
    match.toLowerCase() === 'https' ? 'wss' : 'ws',
  );
  return `${base}${websocketPath}?ticket=${encodeURIComponent(ticket)}`;
}

class DesktopEnvironmentHost {
  constructor({ readSettings, writeSettings, getApiUrl, notifyReady, notifyDisconnected }) {
    this.readSettings = readSettings;
    this.writeSettings = writeSettings;
    this.getApiUrl = getApiUrl;
    this.notifyReady = notifyReady;
    this.notifyDisconnected = notifyDisconnected;
    this.socket = null;
    this.bindingId = null;
  }

  config() {
    const settings = this.readSettings();
    let deviceId = String(settings.desktopEnvironmentDeviceId || '').trim();
    if (!deviceId) {
      deviceId = crypto.randomUUID().replace(/-/g, '');
      this.writeSettings({ desktopEnvironmentDeviceId: deviceId });
    }
    return {
      enabled: settings.desktopEnvironmentEnabled === true,
      deviceId,
      deviceName: os.hostname() || 'Integral Desktop',
      bindingId: this.bindingId,
      roots: this.roots().map(({ id, label }) => ({ id, label })),
    };
  }

  setEnabled(enabled) {
    this.writeSettings({ desktopEnvironmentEnabled: enabled === true });
    if (!enabled) this.disconnect();
  }

  roots() {
    const roots = this.readSettings().desktopEnvironmentRoots;
    if (!Array.isArray(roots)) return [];
    return roots.flatMap((item) => {
      if (!item || typeof item !== 'object') return [];
      const rootPath = String(item.path || '').trim();
      if (!rootPath || !path.isAbsolute(rootPath)) return [];
      return [
        {
          id: String(item.id || rootId(rootPath)),
          label: cleanLabel(item.label) || path.basename(rootPath) || rootPath,
          path: rootPath,
        },
      ];
    });
  }

  addRoot(rootPath) {
    const resolved = fs.realpathSync(rootPath);
    const roots = this.roots();
    if (!roots.some((root) => root.path === resolved)) {
      roots.push({
        id: rootId(resolved),
        label: path.basename(resolved) || resolved,
        path: resolved,
      });
      this.writeSettings({ desktopEnvironmentRoots: roots });
    }
    return roots;
  }

  connect(session) {
    if (!this.config().enabled) throw new Error('Desktop environment access is disabled');
    const ticket = String(session?.ticket || '');
    const websocketPath = String(session?.websocket_path || '');
    if (!ticket || !websocketPath.startsWith('/ws/')) {
      throw new Error('Invalid desktop environment session');
    }
    this.disconnect();
    const socket = new WebSocket(websocketUrl(this.getApiUrl(), websocketPath, ticket));
    this.socket = socket;
    return new Promise((resolve, reject) => {
      let settled = false;
      const timeout = setTimeout(() => {
        if (settled) return;
        settled = true;
        socket.close();
        reject(new Error('Desktop environment handshake timed out'));
      }, 10_000);
      const settleReady = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        this.notifyReady();
        resolve(true);
      };
      socket.addEventListener('message', (event) => {
        void this.onMessage(socket, event.data, settleReady);
      });
      socket.addEventListener('close', () => {
        if (!settled) {
          settled = true;
          clearTimeout(timeout);
          reject(new Error('Desktop environment disconnected before it was ready'));
        }
        if (this.socket !== socket) return;
        this.socket = null;
        this.bindingId = null;
        this.notifyDisconnected();
      });
      socket.addEventListener('error', () => {
        // close owns state cleanup and rejects an incomplete handshake.
      });
    });
  }

  disconnect() {
    const socket = this.socket;
    this.socket = null;
    this.bindingId = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
  }

  async onMessage(socket, raw, onReady = () => {}) {
    let message;
    try {
      message = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (message.type === 'ready') {
      this.bindingId = String(message.binding_id || '') || null;
      if (this.bindingId) onReady();
      return;
    }
    if (message.type !== 'invoke' || !message.id) return;
    try {
      const data = await this.invoke(String(message.tool || ''), message.arguments || {});
      socket.send(JSON.stringify({ type: 'result', id: message.id, ok: true, data }));
    } catch (error) {
      const safeCode = String(error.code || '').startsWith('environment.')
        ? String(error.code)
        : 'environment.adapter_failed';
      const safeMessage =
        safeCode === 'environment.adapter_failed'
          ? 'Desktop capability failed'
          : error.message || 'Desktop capability failed';
      socket.send(
        JSON.stringify({
          type: 'result',
          id: message.id,
          ok: false,
          error: {
            code: safeCode,
            message: safeMessage,
          },
        }),
      );
    }
  }

  rootFor(rootIdValue) {
    const root = this.roots().find((item) => item.id === String(rootIdValue || ''));
    if (!root) {
      const error = new Error('The requested desktop root is not granted');
      error.code = 'environment.permission_denied';
      throw error;
    }
    return root;
  }

  async resolveGrantedPath(root, relativePath = '') {
    const raw = String(relativePath || '');
    if (path.isAbsolute(raw) || raw.split(/[\\/]+/).includes('..')) {
      const error = new Error('Path escapes the granted desktop root');
      error.code = 'environment.path_outside_grant';
      throw error;
    }
    const canonicalRoot = await fsp.realpath(root.path);
    const candidate = await fsp.realpath(path.resolve(canonicalRoot, raw || '.'));
    const relative = path.relative(canonicalRoot, candidate);
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      const error = new Error('Path escapes the granted desktop root');
      error.code = 'environment.path_outside_grant';
      throw error;
    }
    return { absolute: candidate, relative: relative.split(path.sep).join('/') };
  }

  async walk(root, startPath, visit) {
    const start = await this.resolveGrantedPath(root, startPath);
    const canonicalRoot = await fsp.realpath(root.path);
    const queue = [start.absolute];
    let scanned = 0;
    while (queue.length && scanned < MAX_SCAN_ENTRIES) {
      const current = queue.shift();
      const entries = await fsp.readdir(current, { withFileTypes: true });
      for (const entry of entries) {
        scanned += 1;
        if (scanned > MAX_SCAN_ENTRIES) break;
        const absolute = path.join(current, entry.name);
        const relative = path.relative(canonicalRoot, absolute).split(path.sep).join('/');
        const shouldStop = await visit({ absolute, relative, entry });
        if (shouldStop) return;
        if (entry.isDirectory() && !entry.isSymbolicLink()) queue.push(absolute);
      }
    }
  }

  async invoke(tool, args) {
    if (tool === 'desktop__diagnostics') {
      return {
        platform: process.platform,
        arch: process.arch,
        release: os.release(),
        hostname: os.hostname(),
        desktop_version: process.env.npm_package_version || null,
      };
    }
    if (tool === 'desktop__list_roots') {
      return { roots: this.roots().map(({ id, label }) => ({ id, label })) };
    }
    const root = this.rootFor(args.root_id);
    if (tool === 'desktop__list_directory') {
      const target = await this.resolveGrantedPath(root, args.path);
      const entries = await fsp.readdir(target.absolute, { withFileTypes: true });
      return {
        root_id: root.id,
        path: target.relative,
        truncated: entries.length > MAX_LIST_ITEMS,
        entries: entries.slice(0, MAX_LIST_ITEMS).map((entry) => ({
          name: entry.name,
          kind: entry.isDirectory() ? 'directory' : entry.isFile() ? 'file' : 'other',
        })),
      };
    }
    if (tool === 'desktop__read_file') {
      const target = await this.resolveGrantedPath(root, args.path);
      const stat = await fsp.stat(target.absolute);
      if (!stat.isFile()) throw new Error('Path is not a file');
      if (stat.size > MAX_READ_BYTES) {
        const error = new Error(`File exceeds the ${MAX_READ_BYTES}-byte read limit`);
        error.code = 'environment.result_too_large';
        throw error;
      }
      return {
        root_id: root.id,
        path: target.relative,
        content: await fsp.readFile(target.absolute, 'utf8'),
      };
    }
    if (tool === 'desktop__find_path') {
      const query = String(args.query || '').toLocaleLowerCase();
      if (!query) throw new Error('query is required');
      const matches = [];
      await this.walk(root, args.path || '', async ({ relative, entry }) => {
        if (entry.name.toLocaleLowerCase().includes(query)) matches.push(relative);
        return matches.length >= MAX_FIND_RESULTS;
      });
      return { root_id: root.id, matches, truncated: matches.length >= MAX_FIND_RESULTS };
    }
    if (tool === 'desktop__grep') {
      const pattern = String(args.pattern || '').toLocaleLowerCase();
      if (!pattern) throw new Error('pattern is required');
      const matches = [];
      await this.walk(root, args.path || '', async ({ absolute, relative, entry }) => {
        if (!entry.isFile() || entry.isSymbolicLink()) return false;
        const stat = await fsp.stat(absolute);
        if (stat.size > MAX_SCAN_FILE_BYTES) return false;
        let content;
        try {
          content = await fsp.readFile(absolute, 'utf8');
        } catch {
          return false;
        }
        const lines = content.split(/\r?\n/);
        for (let index = 0; index < lines.length; index += 1) {
          if (lines[index].toLocaleLowerCase().includes(pattern)) {
            matches.push({ path: relative, line: index + 1, text: lines[index].slice(0, 500) });
            if (matches.length >= MAX_GREP_RESULTS) return true;
          }
        }
        return false;
      });
      return { root_id: root.id, matches, truncated: matches.length >= MAX_GREP_RESULTS };
    }
    const error = new Error('Desktop capability is not implemented');
    error.code = 'environment.capability_revoked';
    throw error;
  }
}

module.exports = { DesktopEnvironmentHost };
