'use strict';

const assert = require('node:assert/strict');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

const {
  grantFromMemory,
  grantMemoryPath,
  isLiveGrantMemory,
  publicGrantConfig,
  readGrantMemory,
  rememberApprovedGrant,
  rememberExpiredGrant,
  rememberRevokedGrant,
} = require('../src/computer-use-grant-store');

test('approved grants remember apps and duration across process restarts', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-grant-memory-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const filePath = grantMemoryPath(root);
  rememberApprovedGrant(
    filePath,
    {
      id: 'grant-1',
      approvedAt: '2026-09-30T12:00:00.000Z',
      expiresAt: '2026-09-30T13:00:00.000Z',
      screenshotsAllowed: true,
      actionsAllowed: true,
      apps: [{ pid: 764, name: 'Calculator', bundleId: 'com.apple.calculator' }],
    },
    60,
  );

  const remembered = readGrantMemory(filePath);
  assert.equal(remembered.durationMinutes, 60);
  assert.deepEqual(remembered.apps, [
    { name: 'Calculator', bundleId: 'com.apple.calculator' },
  ]);
  assert.equal(
    isLiveGrantMemory(remembered, new Date('2026-09-30T12:30:00.000Z')),
    true,
  );
  const restored = grantFromMemory(
    remembered,
    'binding-2',
    new Date('2026-09-30T12:30:00.000Z'),
  );
  assert.equal(restored.bindingGeneration, 'binding-2');
  assert.equal(restored.expiresAt, '2026-09-30T13:00:00.000Z');
  assert.equal(restored.apps[0].pid, undefined);
});

test('revoke keeps the last app list but stops restoring the live lease', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-grant-memory-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const filePath = grantMemoryPath(root);
  rememberApprovedGrant(
    filePath,
    {
      id: 'grant-1',
      approvedAt: '2026-09-30T12:00:00.000Z',
      expiresAt: '2026-09-30T13:00:00.000Z',
      screenshotsAllowed: true,
      actionsAllowed: false,
      apps: [{ name: 'Calculator', bundleId: 'com.apple.calculator' }],
    },
    30,
  );
  rememberRevokedGrant(filePath);
  const remembered = readGrantMemory(filePath);
  assert.equal(remembered.revoked, true);
  assert.equal(remembered.apps[0].name, 'Calculator');
  assert.equal(remembered.durationMinutes, 30);
  assert.equal(
    isLiveGrantMemory(remembered, new Date('2026-09-30T12:10:00.000Z')),
    false,
  );
  assert.equal(
    publicGrantConfig(remembered, null).expiresAt,
    null,
  );
});

test('expiry keeps remembered apps without a live restore', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-grant-memory-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const filePath = grantMemoryPath(root);
  rememberApprovedGrant(
    filePath,
    {
      id: 'grant-1',
      approvedAt: '2026-09-30T12:00:00.000Z',
      expiresAt: '2026-09-30T13:00:00.000Z',
      screenshotsAllowed: false,
      actionsAllowed: false,
      apps: [{ name: 'Notes', bundleId: 'com.apple.Notes' }],
    },
    15,
  );
  rememberExpiredGrant(filePath, new Date('2026-09-30T13:00:00.000Z'));
  const remembered = readGrantMemory(filePath);
  assert.equal(
    isLiveGrantMemory(remembered, new Date('2026-09-30T13:00:01.000Z')),
    false,
  );
  assert.equal(publicGrantConfig(remembered, null).apps[0].name, 'Notes');
  assert.equal(publicGrantConfig(remembered, null).durationMinutes, 15);
});
