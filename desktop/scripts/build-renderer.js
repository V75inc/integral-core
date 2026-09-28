'use strict';

/**
 * Build the web frontend for the desktop shell and copy it into
 * `desktop/renderer/` (see copy-renderer.js).
 *
 * Sets INTEGRAL_DESKTOP_BUILD=1 so vite.config.ts emits relative asset URLs
 * (`base: './'`) suitable for loading from file://. Bakes VITE_API_URL only
 * as a fallback for a bridgeless context — inside the shell the preload
 * bridge (`window.integralDesktop.getApiUrl()`) always wins; honour an
 * explicitly-provided VITE_API_URL so remote-backend bundles are possible.
 *
 * Env is set in-process (not via shell `VAR=x` prefixes) so this works on
 * Windows as well as macOS/Linux.
 */

const path = require('node:path');
const { execFileSync } = require('node:child_process');

const root = path.join(__dirname, '..');
const frontendDir = path.join(root, '..', 'frontend');

const env = {
  ...process.env,
  INTEGRAL_DESKTOP_BUILD: '1',
  VITE_API_URL:
    process.env.VITE_API_URL && process.env.VITE_API_URL.trim()
      ? process.env.VITE_API_URL.trim()
      : 'http://localhost:4000',
};

function resolveNpmInvocation({
  platform = process.platform,
  execPath = process.execPath,
  npmExecPath = process.env.npm_execpath,
} = {}) {
  // npm exposes the path to npm-cli.js to lifecycle scripts. Running that
  // JavaScript entry point through Node works uniformly on every platform and
  // avoids Windows' spawnSync EINVAL when execFileSync targets npm.cmd.
  if (npmExecPath) {
    return {
      file: execPath,
      prefixArgs: [npmExecPath],
      shell: false,
    };
  }

  // Preserve direct `node scripts/build-renderer.js` usage. Windows command
  // shims require a shell when no npm lifecycle supplied npm_execpath.
  return {
    file: platform === 'win32' ? 'npm.cmd' : 'npm',
    prefixArgs: [],
    shell: platform === 'win32',
  };
}

function buildRenderer() {
  const npm = resolveNpmInvocation();
  execFileSync(
    npm.file,
    [...npm.prefixArgs, 'run', 'build', '--prefix', frontendDir],
    {
      env,
      stdio: 'inherit',
      cwd: root,
      shell: npm.shell,
    },
  );

  execFileSync(process.execPath, [path.join(__dirname, 'copy-renderer.js')], {
    stdio: 'inherit',
    cwd: root,
  });
}

if (require.main === module) buildRenderer();

module.exports = { buildRenderer, resolveNpmInvocation };
