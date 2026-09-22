'use strict';

/**
 * Copy the built web frontend (`../frontend/dist`, produced by
 * `npm run build --prefix ../frontend`) into `desktop/renderer/`, which is
 * what `src/main.js` loads in prod and what electron-builder packages.
 *
 * A copy (rather than pointing electron-builder at `../frontend/dist`) keeps
 * packaged paths stable and lets `renderer/` stay gitignored build output.
 */

const path = require('node:path');
const fs = require('node:fs');

const root = path.join(__dirname, '..');
const from = path.join(root, '..', 'frontend', 'dist');
const to = path.join(root, 'renderer');

if (!fs.existsSync(path.join(from, 'index.html'))) {
  console.error(`copy-renderer: frontend build not found at ${from}`);
  console.error('copy-renderer: run `npm run build --prefix ../frontend` first.');
  process.exit(1);
}

fs.rmSync(to, { recursive: true, force: true });
fs.cpSync(from, to, { recursive: true });
console.log(`copy-renderer: ${from} -> ${to}`);
