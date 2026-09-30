'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const releaseDir = path.join(__dirname, '..', 'release');

function findAppDirectory(root, depth = 0) {
  if (depth > 5 || !fs.existsSync(root)) return null;
  if (
    path.basename(root) === 'app' &&
    fs.existsSync(path.join(root, 'package.json')) &&
    fs.existsSync(path.join(root, 'node_modules', '@trycua', 'cua-driver'))
  ) {
    return root;
  }
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const found = findAppDirectory(path.join(root, entry.name), depth + 1);
    if (found) return found;
  }
  return null;
}

const appDir = findAppDirectory(releaseDir);
if (!appDir) {
  throw new Error('Could not find packaged Integral application resources');
}

let executable;
if (process.platform === 'darwin') {
  executable = path.join(appDir, '..', '..', 'MacOS', 'Integral');
} else {
  const unpackedRoot = path.join(appDir, '..', '..');
  executable = path.join(
    unpackedRoot,
    process.platform === 'win32' ? 'Integral.exe' : 'integral',
  );
}
if (!fs.existsSync(executable)) {
  throw new Error(`Could not find packaged Electron executable: ${executable}`);
}

const result = spawnSync(
  executable,
  [
    '-e',
    "import('@trycua/cua-driver').then(m=>{if(!m.CuaDriver)process.exit(2);console.log('packaged-sdk-ok')}).catch(e=>{console.error(e);process.exit(1)})",
  ],
  {
    cwd: appDir,
    encoding: 'utf8',
    timeout: 30_000,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
  },
);
process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
if (result.status !== 0) {
  throw new Error(`Packaged Cua SDK smoke failed with status ${result.status}`);
}
