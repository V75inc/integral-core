'use strict';

/**
 * Checksum-verified provisioning of the Cua Driver bundled runtime.
 *
 * Why this exists and why it is not just "run the vendor's installer":
 *
 *   1. The vendor installer is a two-stage `curl | bash` — `install.sh` fetches
 *      `_install-rust.sh` and pipes it to a shell. Delegating to that means
 *      running an unpinned remote script.
 *   2. **The vendor installer performs no integrity verification at all.**
 *      Grepping `_install-rust.sh` for `sha256|shasum|checksum|integrity`
 *      returns nothing. It downloads a ~34 MB executable and execs it. The
 *      releases *do* publish a `checksums.txt` in standard sha256sum format —
 *      it is simply never read.
 *   3. On Linux the vendor installer edits the user's shell rc. An app doing
 *      that silently is not shippable.
 *
 * So this module downloads the platform asset itself, verifies it against
 * `checksums.txt`, and unpacks it into Electron's own `userData`. No shell, no
 * pipe, no PATH mutation, no sudo, no administrator elevation.
 *
 * This module is used by release provisioning, not at runtime. The verified
 * executable is copied into `resources/driver` and then signed/packaged with
 * Integral Desktop. End users never run this download path.
 *
 * ## Pinning
 *
 * `CUA_DRIVER_PINNED_VERSION` pins an exact release for reproducible builds.
 * Unset, the installer resolves the newest non-prerelease `cua-driver-rs` tag
 * from the GitHub releases API. Note that today the newest
 * `cua-driver-rs-*` tags are themselves marked `prerelease: true` upstream, so
 * an unpinned resolve can legitimately select a nightly. Pin for releases.
 */

const crypto = require('node:crypto');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { pipeline } = require('node:stream/promises');
const { promisify } = require('node:util');
const { spawn } = require('node:child_process');

const execFileAsync = promisify(execFile);

const RELEASES_API = 'https://api.github.com/repos/trycua/cua/releases?per_page=100';
const DOWNLOAD_BASE = 'https://github.com/trycua/cua/releases/download';
const MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024;
const DOWNLOAD_TIMEOUT_MS = 10 * 60 * 1000;
const ARCHIVE_TIMEOUT_MS = 60 * 1000;

/** Asset label, matching the vendor's own naming. */
function platformLabel(platform = process.platform, arch = process.arch) {
  if (platform === 'darwin') {
    return ['x64', 'arm64'].includes(arch) ? 'darwin-universal' : null;
  }
  if (platform === 'linux') {
    return { x64: 'linux-x86_64', arm64: 'linux-arm64' }[arch] || null;
  }
  if (platform === 'win32') {
    return { x64: 'windows-x86_64', arm64: 'windows-arm64' }[arch] || null;
  }
  return null;
}

/** The `-binary` assets are the bare executable; the plain ones carry the .app. */
function assetName(version, label) {
  const ext = label.startsWith('windows-') ? 'zip' : 'tar.gz';
  return `cua-driver-rs-${version}-${label}-binary.${ext}`;
}

function isSupported(platform = process.platform, arch = process.arch) {
  return platformLabel(platform, arch) !== null;
}

function get(url, { headers = {}, timeoutMs = 30_000 } = {}) {
  return new Promise((resolve, reject) => {
    const request = https.get(
      url,
      { headers: { 'user-agent': 'integral-desktop', ...headers }, timeout: timeoutMs },
      (response) => {
        // Follow redirects manually so the final URL is observable and a
        // redirect to a non-GitHub host is a hard failure, not a silent hop.
        if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
          response.resume();
          resolve(get(new URL(response.headers.location, url).toString(), { headers, timeoutMs }));
          return;
        }
        if (response.statusCode !== 200) {
          response.resume();
          reject(new Error(`GET ${url} → ${response.statusCode}`));
          return;
        }
        resolve({ status: response.statusCode, headers: response.headers, stream: response });
      },
    );
    request.on('timeout', () => request.destroy(new Error('request timed out')));
    request.on('error', reject);
  });
}

async function getText(url, options = {}) {
  const response = await get(url, options);
  const chunks = [];
  let total = 0;
  for await (const chunk of response.stream) {
    total += chunk.length;
    if (total > 8 * 1024 * 1024) throw new Error('metadata response too large');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}

/** Parse standard `sha256sum` output: `<64 hex>  <name>`. */
function parseChecksums(text) {
  const map = new Map();
  for (const rawLine of String(text || '').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const match = line.match(/^([0-9a-f]{64})\s+\*?(.+?)\s*$/i);
    if (match) map.set(match[2], match[1].toLowerCase());
  }
  return map;
}

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('error', reject);
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

/**
 * Resolve an exact release version.
 *
 * Prefers `CUA_DRIVER_PINNED_VERSION` — set it for releases, because an
 * unpinned resolve is not reproducible.
 *
 * Unpinned, we prefer a non-prerelease `cua-driver-rs-v*` tag, but there is a
 * live wrinkle: **as of 0.30.4 every `cua-driver-rs` release is marked
 * `prerelease: true` upstream**, so a strict stable-only filter matches nothing
 * and auto-install would be dead on arrival. We therefore fall back to the
 * newest prerelease and say so via `fromPrerelease`, which the consent dialog
 * surfaces — a user is entitled to know the artifact is flagged pre-release.
 * Nightly tags (`nightly-cua-driver-rs-…`) are excluded either way.
 */
async function resolveVersion({ pinnedVersion = '', fetchJson = getText } = {}) {
  const pin = String(pinnedVersion || '').trim();
  if (pin) {
    const normalized = pin.replace(/^v/, '');
    if (!/^\d+\.\d+\.\d+/.test(normalized)) {
      throw new Error(`pinned version "${pin}" is not an exact x.y.z release`);
    }
    return { version: normalized, tag: `cua-driver-rs-v${normalized}`, pinned: true, fromPrerelease: false };
  }

  const releases = JSON.parse(await fetchJson(RELEASES_API));
  const tags = releases
    .filter((release) => typeof release?.tag_name === 'string')
    // `cua-driver-rs-v<semver>` only: excludes nightlies and other products.
    .filter((release) => /^cua-driver-rs-v\d+\.\d+\.\d+/.test(release.tag_name))
    .map((release) => ({
      tag: release.tag_name,
      version: release.tag_name.replace(/^cua-driver-rs-v/, ''),
      prerelease: release.prerelease === true,
    }));
  if (!tags.length) {
    throw new Error('no cua-driver-rs release found');
  }

  const stable = tags.find((tag) => !tag.prerelease);
  const chosen = stable || tags[0];
  return {
    version: chosen.version,
    tag: chosen.tag,
    pinned: false,
    fromPrerelease: !stable,
  };
}

async function downloadVerified(
  url,
  destination,
  expectedSha256,
  { onProgress, fetchStream = get } = {},
) {
  const response = await fetchStream(url, { timeoutMs: DOWNLOAD_TIMEOUT_MS });
  const declared = Number(response.headers['content-length'] || 0);
  if (declared && declared > MAX_DOWNLOAD_BYTES) {
    response.stream.resume();
    throw new Error(`asset is ${declared} bytes, above the ${MAX_DOWNLOAD_BYTES} cap`);
  }

  await fsp.mkdir(path.dirname(destination), { recursive: true });
  let received = 0;
  const counter = async function* (source) {
    for await (const chunk of source) {
      received += chunk.length;
      if (received > MAX_DOWNLOAD_BYTES) throw new Error('download exceeded the size cap');
      onProgress?.(received, declared);
      yield chunk;
    }
  };
  await pipeline(response.stream, counter, fs.createWriteStream(destination));

  const actual = await sha256File(destination);
  if (actual !== String(expectedSha256).toLowerCase()) {
    // Never leave an unverified artifact on disk where a later run might pick
    // it up as if it had been checked.
    await fsp.rm(destination, { force: true });
    throw new Error(
      `checksum mismatch for ${path.basename(destination)}: ` +
        `expected ${expectedSha256}, got ${actual}`,
    );
  }
  return { path: destination, bytes: received, sha256: actual };
}

/**
 * Unpack the verified archive. Uses the platform's own extractor with a
 * fixed argv — no shell, so nothing in the filename is ever interpreted.
 */
async function extract(
  archivePath,
  targetDir,
  { platform = process.platform, run = execFileAsync } = {},
) {
  await fsp.mkdir(targetDir, { recursive: true });
  if (archivePath.endsWith('.zip')) {
    // Keep paths in environment values rather than appending them after a
    // string-valued `-Command` (PowerShell would interpret those as more
    // command text instead of populating `$args`).
    await run(
      'powershell.exe',
      [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath $env:INTEGRAL_ARCHIVE -DestinationPath $env:INTEGRAL_TARGET -Force",
      ],
      {
        timeout: ARCHIVE_TIMEOUT_MS,
        windowsHide: true,
        env: {
          ...process.env,
          INTEGRAL_ARCHIVE: archivePath,
          INTEGRAL_TARGET: targetDir,
        },
      },
    );
    return targetDir;
  }
  await run('tar', ['-xzf', archivePath, '-C', targetDir], {
    timeout: ARCHIVE_TIMEOUT_MS,
  });
  return targetDir;
}

/** The archives contain one `cua-driver` executable, possibly nested. */
async function findExecutable(root, { platform = process.platform } = {}) {
  const name = platform === 'win32' ? 'cua-driver.exe' : 'cua-driver';
  const stack = [root];
  while (stack.length) {
    const dir = stack.shift();
    let entries;
    try {
      entries = await fsp.readdir(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name === name) return full;
    }
  }
  return null;
}

/**
 * Download, verify, and unpack the driver into `userData/driver`.
 *
 * Idempotent: returns `{changed: false}` when an already-installed binary is
 * present and matches the resolved version.
 */
async function installDriver({
  installDir,
  platform = process.platform,
  arch = process.arch,
  pinnedVersion = process.env.CUA_DRIVER_PINNED_VERSION || '',
  fetchText = getText,
  onProgress = () => {},
  signal,
} = {}) {
  if (!installDir) throw new Error('installDir is required');
  if (!isSupported(platform, arch)) {
    throw new Error(
      `bundled runtime provisioning is not available on ${platform}/${arch}`,
    );
  }

  const label = platformLabel(platform, arch);
  const { version, tag, pinned } = await resolveVersion({ pinnedVersion, fetchJson: fetchText });
  const name = assetName(version, label);
  const targetBinary = path.join(installDir, platform === 'win32' ? 'cua-driver.exe' : 'cua-driver');

  // Already at this version? Do not re-download 34 MB to learn nothing.
  const stampPath = path.join(installDir, '.installed-version');
  const digestPath = path.join(installDir, '.installed-sha256');
  try {
    if ((await fsp.readFile(stampPath, 'utf8')).trim() === version) {
      const stat = await fsp.stat(targetBinary).catch(() => null);
      const recordedDigest = (await fsp.readFile(digestPath, 'utf8')).trim().toLowerCase();
      if (
        stat?.isFile() &&
        /^[0-9a-f]{64}$/.test(recordedDigest) &&
        (await sha256File(targetBinary)) === recordedDigest
      ) {
        return { changed: false, version, tag, pinned, path: targetBinary, source: 'already-installed' };
      }
    }
  } catch {
    // No stamp — fall through to a real install.
  }

  const checksums = parseChecksums(
    await fetchText(`${DOWNLOAD_BASE}/${tag}/checksums.txt`),
  );
  const expected = checksums.get(name);
  if (!expected) {
    throw new Error(
      `${name} is not listed in ${tag}/checksums.txt — refusing to install an unverified binary`,
    );
  }

  const workDir = path.join(installDir, '.staging');
  await fsp.rm(workDir, { recursive: true, force: true });
  await fsp.mkdir(workDir, { recursive: true });

  try {
    const archive = path.join(workDir, name);
    const downloaded = await downloadVerified(
      `${DOWNLOAD_BASE}/${tag}/${encodeURIComponent(name)}`,
      archive,
      expected,
      { onProgress },
    );
    signal?.throwIfAborted?.();

    const unpacked = await extract(archive, workDir, { platform });
    const binary = await findExecutable(unpacked, { platform });
    if (!binary) {
      throw new Error(`${name} did not contain a cua-driver executable`);
    }

    await fsp.mkdir(installDir, { recursive: true });
    // Keep the active executable intact until the replacement has been copied,
    // permissioned, and hashed in the same directory.
    const replacement = path.join(
      installDir,
      `.${path.basename(targetBinary)}.${process.pid}.replacement`,
    );
    await fsp.copyFile(binary, replacement);
    if (platform !== 'win32') await fsp.chmod(replacement, 0o755);
    const binarySha256 = await sha256File(replacement);
    await fsp.rename(replacement, targetBinary);
    await fsp.writeFile(stampPath, `${version}\n`, 'utf8');
    await fsp.writeFile(digestPath, `${binarySha256}\n`, 'utf8');
    await fsp.rm(workDir, { recursive: true, force: true });

    return {
      changed: true,
      version,
      tag,
      pinned,
      path: targetBinary,
      source: 'verified-download',
      sha256: downloaded.sha256,
      binarySha256,
      bytes: downloaded.bytes,
    };
  } finally {
    // Staging never outlives a failure, successful or not.
    await fsp.rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
}

module.exports = {
  installDriver,
  resolveVersion,
  parseChecksums,
  assetName,
  platformLabel,
  isSupported,
  downloadVerified,
  extract,
  get,
  RELEASES_API,
  DOWNLOAD_BASE,
};
