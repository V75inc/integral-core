'use strict';

const assert = require('node:assert/strict');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

const {
  applyJevSettingsPatch,
  jevSettingsPath,
  loadJevRuntimeSettings,
  publicJevConfig,
  readJevSettingsRecord,
} = require('../src/jev-settings');

function codec() {
  return {
    encrypt: (value) => Buffer.from(value, 'utf8').toString('base64'),
    decrypt: (value) => Buffer.from(value, 'base64').toString('utf8'),
  };
}

test('Jev settings persist enabled plus an encrypted key without returning it in public config', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-jev-settings-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const filePath = jevSettingsPath(root);
  const saved = applyJevSettingsPatch(
    filePath,
    { enabled: true, apiKey: 'sk-typesafe-secret' },
    codec(),
  );
  assert.equal(saved.jevEnabled, true);
  assert.equal(saved.jevConfigured, true);
  const record = readJevSettingsRecord(filePath);
  assert.ok(record.apiKeyCipher);
  assert.notEqual(record.apiKeyCipher, 'sk-typesafe-secret');
  assert.deepEqual(publicJevConfig(record), {
    jevEnabled: true,
    jevConfigured: true,
  });
  const runtime = loadJevRuntimeSettings(filePath, codec());
  assert.equal(runtime.apiKey, 'sk-typesafe-secret');
});

test('enabling Jev without a key is refused', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-jev-settings-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  assert.throws(
    () =>
      applyJevSettingsPatch(
        jevSettingsPath(root),
        { enabled: true },
        codec(),
      ),
    (error) => error.code === 'computer_use.jev_key_required',
  );
});
