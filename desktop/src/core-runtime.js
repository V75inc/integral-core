'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

function validateWorkspace(raw) {
  const url = new URL(raw);
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.username || url.password || url.hash || url.search ||
      !(url.protocol === 'https:' || (url.protocol === 'http:' && loopback))) {
    throw new Error('Use an HTTPS workspace URL, or HTTP on this computer.');
  }
  return url.href.replace(/\/$/, '');
}

function invocation({ resources, command = process.env.INTEGRAL_CORE_COMMAND,
                      prefix = process.env.INTEGRAL_CORE_ARGS }) {
  if (command) {
    const args = prefix ? JSON.parse(prefix) : [];
    if (!Array.isArray(args) || args.some(value => typeof value !== 'string')) {
      throw new Error('INTEGRAL_CORE_ARGS must be a JSON array of arguments.');
    }
    return { file: command, args };
  }
  const manifestPath = path.join(resources, 'core', 'runtime.json');
  if (fs.existsSync(manifestPath)) {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const allowed = ['contract_version', 'coreWheel', 'platform', 'arch', 'sha256', 'harness_provider_id'];
    if (!manifest || manifest.contract_version !== 1 || Object.keys(manifest).some(key => !allowed.includes(key))) {
      throw new Error('Invalid bundled runtime manifest.');
    }
    if (manifest.platform !== process.platform || manifest.arch !== process.arch) {
      throw new Error('The bundled Core runtime targets a different platform.');
    }
    for (const name of [manifest.coreWheel]) {
      if (typeof name !== 'string' || path.basename(name) !== name || !name.endsWith('.whl')) {
        throw new Error('Invalid bundled runtime manifest.');
      }
    }
    const base = path.dirname(manifestPath);
    const uvName = process.platform === 'win32' ? 'uv.exe' : 'uv';
    for (const [name, hashKey] of [[manifest.coreWheel, manifest.coreWheel], [uvName, 'uv']]) {
      const expected = manifest.sha256?.[hashKey];
      if (typeof expected !== 'string' || !/^[a-f0-9]{64}$/.test(expected) ||
          crypto.createHash('sha256').update(fs.readFileSync(path.join(base, name))).digest('hex') !== expected) {
        throw new Error('The bundled runtime failed its integrity check. Reinstall Integral.');
      }
    }
    return {
      file: path.join(base, process.platform === 'win32' ? 'uv.exe' : 'uv'),
      args: ['tool', 'run', '--python', '3.12', '--index-url', 'https://pypi.org/simple',
        '--from', path.join(base, manifest.coreWheel), 'integral'],
    };
  }
  return { file: 'integral', args: [] };
}

function runCli(command, args, { timeout = 300_000, spawnProcess = spawn } = {}) {
  return new Promise((resolve, reject) => {
    const env = { ...process.env };
    delete env.PYTHONPATH;
    const child = spawnProcess(command.file, [...command.args, ...args], {
      env, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('Core startup timed out. Inspect the installation logs and retry.'));
    }, timeout);
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-8000); });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0) return reject(new Error(stderr.trim() || `Core exited with code ${code}.`));
      try { resolve(JSON.parse(stdout)); } catch { reject(new Error('Core returned an invalid runtime descriptor.')); }
    });
  });
}

async function startCore({ home, resources, command, runner = runCli }) {
  const cli = command || invocation({ resources });
  const args = ['up', '--home', home, '--no-open', '--json'];
  const runtime = await runner(cli, args);
  if (runtime.contract_version !== 1 || runtime.state !== 'ready' || !runtime.installation_id || typeof runtime.started !== 'boolean') {
    throw new Error('This Core release does not support the desktop runtime contract.');
  }
  if (runtime.harness_provider_id !== 'integral_native') {
    throw new Error('This Core runtime predates the native-only harness. Upgrade Core and restart the installation.');
  }
  const workspace = validateWorkspace(runtime.web_url);
  if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(workspace).hostname)) {
    throw new Error('The local runtime returned a nonlocal workspace.');
  }
  return { ...runtime, web_url: workspace, cli, owned: runtime.started };
}

module.exports = { validateWorkspace, invocation, runCli, startCore };
