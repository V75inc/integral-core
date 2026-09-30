'use strict';

const assert = require('node:assert/strict');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  installDriver,
  resolveVersion,
  parseChecksums,
  assetName,
  platformLabel,
  isSupported,
  downloadVerified,
  extract,
} = require('../src/driver-install');

const CHECKSUMS = [
  '## SHA256 Checksums',
  '```',
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  cua-driver-rs-0.30.4-linux-x86_64-binary.tar.gz',
  'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  cua-driver-rs-0.30.4-windows-x86_64-binary.zip',
  '```',
].join('\n');

const RELEASES = JSON.stringify([
  { tag_name: 'nightly-cua-driver-rs-v0.31.0-nightly.20260929.1', prerelease: true },
  { tag_name: 'cua-driver-rs-v0.30.4', prerelease: true },
  { tag_name: 'cua-driver-rs-v0.29.0', prerelease: false },
  { tag_name: 'sandbox-v0.8.0', prerelease: false },
]);

function fakeStream(buffer) {
  const { Readable } = require('node:stream');
  return Readable.from([buffer]);
}

test('release provisioning supports bundled runtimes on every desktop platform', () => {
  assert.equal(isSupported('darwin', 'arm64'), true);
  assert.equal(platformLabel('darwin', 'arm64'), 'darwin-universal');
  assert.equal(isSupported('linux', 'x64'), true);
  assert.equal(isSupported('win32', 'x64'), true);
  assert.equal(platformLabel('linux', 'riscv64'), null);
});

test('asset names follow the vendor -binary convention', () => {
  assert.equal(
    assetName('0.30.4', 'linux-x86_64'),
    'cua-driver-rs-0.30.4-linux-x86_64-binary.tar.gz',
  );
  assert.equal(
    assetName('0.30.4', 'windows-x86_64'),
    'cua-driver-rs-0.30.4-windows-x86_64-binary.zip',
  );
  assert.equal(
    assetName('0.30.4', 'darwin-universal'),
    'cua-driver-rs-0.30.4-darwin-universal-binary.tar.gz',
  );
});

test('parseChecksums reads sha256sum format and skips fences', () => {
  const map = parseChecksums(CHECKSUMS);
  assert.equal(
    map.get('cua-driver-rs-0.30.4-linux-x86_64-binary.tar.gz'),
    'a'.repeat(64),
  );
  assert.equal(map.size, 2);
  assert.equal(parseChecksums('').size, 0);
});

test('resolveVersion honours an exact pin without hitting the API', async () => {
  let called = false;
  const resolved = await resolveVersion({
    pinnedVersion: '0.30.4',
    fetchJson: async () => {
      called = true;
      return '[]';
    },
  });
  assert.deepEqual(resolved, {
    version: '0.30.4',
    tag: 'cua-driver-rs-v0.30.4',
    pinned: true,
    fromPrerelease: false,
  });
  assert.equal(called, false, 'a pin must not depend on the network');
});

test('resolveVersion rejects a non-exact pin', async () => {
  await assert.rejects(
    resolveVersion({ pinnedVersion: 'latest', fetchJson: async () => '[]' }),
    /not an exact/,
  );
});

test('resolveVersion prefers a stable release when one exists', async () => {
  const resolved = await resolveVersion({ fetchJson: async () => RELEASES });
  assert.equal(resolved.tag, 'cua-driver-rs-v0.29.0');
  assert.equal(resolved.pinned, false);
  assert.equal(resolved.fromPrerelease, false);
});

test('resolveVersion falls back to a prerelease and says so', async () => {
  // Live upstream state as of 0.30.4: every cua-driver-rs release is a
  // prerelease. A stable-only filter would match nothing and auto-install
  // would be dead on arrival, so the fallback must be explicit.
  const onlyPre = JSON.stringify([
    { tag_name: 'nightly-cua-driver-rs-v0.31.0-nightly.1', prerelease: true },
    { tag_name: 'cua-driver-rs-v0.30.4', prerelease: true },
    { tag_name: 'cua-driver-rs-v0.30.3', prerelease: true },
  ]);
  const resolved = await resolveVersion({ fetchJson: async () => onlyPre });
  assert.equal(resolved.tag, 'cua-driver-rs-v0.30.4');
  assert.equal(resolved.fromPrerelease, true, 'consent must be able to disclose this');
});

test('resolveVersion never selects a nightly tag', async () => {
  const nightlies = JSON.stringify([
    { tag_name: 'nightly-cua-driver-rs-v0.99.0-nightly.20260929.1', prerelease: true },
    { tag_name: 'cua-driver-rs-v0.30.4', prerelease: true },
  ]);
  const resolved = await resolveVersion({ fetchJson: async () => nightlies });
  assert.equal(resolved.version, '0.30.4');
  assert.ok(!resolved.tag.includes('nightly'));
});

test('resolveVersion throws when no cua-driver-rs release exists', async () => {
  await assert.rejects(
    resolveVersion({ fetchJson: async () => JSON.stringify([{ tag_name: 'sandbox-v0.8.0' }]) }),
    /no cua-driver-rs release found/,
  );
});

function fakeStream(buffer) {
  const { Readable } = require('node:stream');
  return Readable.from([buffer]);
}

test('downloadVerified rejects a checksum mismatch and removes the file', async (t) => {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-dl-'));
  t.after(() => fsp.rm(dir, { recursive: true, force: true }));
  const destination = path.join(dir, 'asset.bin');
  const payload = Buffer.from('the real driver bytes');

  await assert.rejects(
    downloadVerified('https://example.invalid/asset', destination, 'f'.repeat(64), {
      fetchStream: async () => ({
        status: 200,
        headers: { 'content-length': String(payload.length) },
        stream: fakeStream(payload),
      }),
    }),
    /checksum mismatch/,
  );

  // The critical property: an unverified binary must never be left where a
  // later run could mistake it for a checked one.
  await assert.rejects(
    fsp.stat(destination),
    'a failed verification must not leave an artifact on disk',
  );
});

test('downloadVerified keeps the file when the checksum matches', async (t) => {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-dl-'));
  t.after(() => fsp.rm(dir, { recursive: true, force: true }));
  const destination = path.join(dir, 'asset.bin');
  const payload = Buffer.from('the real driver bytes');
  const expected = require('node:crypto').createHash('sha256').update(payload).digest('hex');

  const progress = [];
  const result = await downloadVerified('https://example.invalid/asset', destination, expected, {
    fetchStream: async () => ({
      status: 200,
      headers: { 'content-length': String(payload.length) },
      stream: fakeStream(payload),
    }),
    onProgress: (received) => progress.push(received),
  });

  assert.equal(result.sha256, expected);
  assert.equal((await fsp.readFile(destination)).toString(), payload.toString());
  assert.ok(progress.length > 0, 'progress must be reported for a UI');
});

test('downloadVerified enforces the size cap before writing', async (t) => {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-dl-'));
  t.after(() => fsp.rm(dir, { recursive: true, force: true }));
  const destination = path.join(dir, 'asset.bin');

  await assert.rejects(
    downloadVerified('https://example.invalid/asset', destination, 'a'.repeat(64), {
      fetchStream: async () => ({
        status: 200,
        headers: { 'content-length': String(600 * 1024 * 1024) },
        stream: fakeStream(Buffer.from('x')),
      }),
    }),
    /above the .* cap/,
  );
  await assert.rejects(fsp.stat(destination));
});

test('Windows extraction passes paths as environment data, never PowerShell command text', async (t) => {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral extract target '));
  t.after(() => fsp.rm(dir, { recursive: true, force: true }));
  const archive = path.join(dir, 'driver archive.zip');
  const target = path.join(dir, 'expanded output');
  await fsp.writeFile(archive, 'fixture');
  const calls = [];

  await extract(archive, target, {
    platform: 'win32',
    run: async (...args) => calls.push(args),
  });

  assert.equal(calls.length, 1);
  const [command, args, options] = calls[0];
  assert.equal(command, 'powershell.exe');
  assert.ok(!args.includes(archive));
  assert.ok(!args.includes(target));
  assert.equal(options.env.INTEGRAL_ARCHIVE, archive);
  assert.equal(options.env.INTEGRAL_TARGET, target);
  assert.match(args.at(-1), /\$env:INTEGRAL_ARCHIVE/);
  assert.match(args.at(-1), /\$env:INTEGRAL_TARGET/);
});

test('installDriver refuses unsupported platform architectures', async () => {
  await assert.rejects(
    installDriver({ installDir: '/tmp/never', platform: 'linux', arch: 'riscv64' }),
    /not available on linux\/riscv64/,
  );
});

test('installDriver refuses an asset missing from checksums.txt', async (t) => {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-inst-'));
  t.after(() => fsp.rm(dir, { recursive: true, force: true }));

  await assert.rejects(
    installDriver({
      installDir: dir,
      platform: 'linux',
      arch: 'x64',
      pinnedVersion: '0.30.4',
      // checksums.txt exists but does not list our asset
      fetchText: async () => CHECKSUMS.replace(/linux-x86_64-binary/, 'linux-arm64-binary'),
    }),
    /not listed in .*checksums\.txt/,
  );
});

test('installDriver is idempotent once the version stamp matches', async (t) => {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-inst-'));
  t.after(() => fsp.rm(dir, { recursive: true, force: true }));
  const binary = Buffer.from('#!/bin/sh\n');
  const binarySha = require('node:crypto').createHash('sha256').update(binary).digest('hex');
  await fsp.writeFile(path.join(dir, 'cua-driver'), binary, { mode: 0o755 });
  await fsp.writeFile(path.join(dir, '.installed-version'), '0.30.4\n', 'utf8');
  await fsp.writeFile(path.join(dir, '.installed-sha256'), `${binarySha}\n`, 'utf8');

  let fetched = false;
  const result = await installDriver({
    installDir: dir,
    platform: 'linux',
    arch: 'x64',
    pinnedVersion: '0.30.4',
    fetchText: async () => {
      fetched = true;
      return CHECKSUMS;
    },
  });

  assert.equal(result.changed, false);
  assert.equal(result.source, 'already-installed');
  assert.equal(fetched, false, 'must not re-download 34 MB to learn nothing');
});

test('installDriver never trusts a matching version stamp without a verified binary digest', async (t) => {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-inst-'));
  t.after(() => fsp.rm(dir, { recursive: true, force: true }));
  await fsp.writeFile(path.join(dir, 'cua-driver'), 'tampered', { mode: 0o755 });
  await fsp.writeFile(path.join(dir, '.installed-version'), '0.30.4\n', 'utf8');

  let fetched = false;
  await assert.rejects(
    installDriver({
      installDir: dir,
      platform: 'linux',
      arch: 'x64',
      pinnedVersion: '0.30.4',
      fetchText: async () => {
        fetched = true;
        return '';
      },
    }),
    /not listed/,
  );
  assert.equal(fetched, true, 'missing integrity metadata must force verified provisioning');
});
