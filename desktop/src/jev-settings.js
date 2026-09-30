'use strict';

const fs = require('node:fs');
const path = require('node:path');

const JEV_SETTINGS_VERSION = 1;

function jevSettingsPath(userDataDir) {
  return path.join(userDataDir, 'computer-use-jev.json');
}

function readJevSettingsRecord(filePath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (!parsed || parsed.version !== JEV_SETTINGS_VERSION) return null;
    return {
      version: JEV_SETTINGS_VERSION,
      enabled: parsed.enabled === true,
      apiKeyCipher: parsed.apiKeyCipher ? String(parsed.apiKeyCipher) : '',
    };
  } catch {
    return null;
  }
}

function writeJevSettingsRecord(filePath, record) {
  const next = {
    version: JEV_SETTINGS_VERSION,
    enabled: record.enabled === true,
    apiKeyCipher: record.apiKeyCipher ? String(record.apiKeyCipher) : '',
  };
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  fs.writeFileSync(filePath, `${JSON.stringify(next, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
  });
  return next;
}

function publicJevConfig(record, { hasKey } = {}) {
  return {
    jevEnabled: record?.enabled === true,
    jevConfigured: hasKey === true || Boolean(record?.apiKeyCipher),
  };
}

function applyJevSettingsPatch(filePath, patch, { encrypt, decrypt }) {
  const current = readJevSettingsRecord(filePath) || {
    version: JEV_SETTINGS_VERSION,
    enabled: false,
    apiKeyCipher: '',
  };
  let apiKeyCipher = current.apiKeyCipher;
  if (patch.clearApiKey === true) {
    apiKeyCipher = '';
  } else if (typeof patch.apiKey === 'string' && patch.apiKey.trim()) {
    apiKeyCipher = encrypt(patch.apiKey.trim());
  }
  const enabled =
    patch.enabled === undefined ? current.enabled : patch.enabled === true;
  if (enabled && !apiKeyCipher) {
    const error = new Error('A TypeSafe API key is required to enable Jev');
    error.code = 'computer_use.jev_key_required';
    throw error;
  }
  const saved = writeJevSettingsRecord(filePath, { enabled, apiKeyCipher });
  return {
    ...publicJevConfig(saved),
    apiKey: saved.apiKeyCipher && decrypt ? decrypt(saved.apiKeyCipher) : '',
  };
}

function loadJevRuntimeSettings(filePath, { decrypt }) {
  const record = readJevSettingsRecord(filePath);
  if (!record) {
    return { enabled: false, apiKey: '', ...publicJevConfig(null) };
  }
  let apiKey = '';
  if (record.apiKeyCipher && decrypt) {
    try {
      apiKey = decrypt(record.apiKeyCipher);
    } catch {
      apiKey = '';
    }
  }
  return {
    enabled: record.enabled === true,
    apiKey,
    ...publicJevConfig(record, { hasKey: Boolean(apiKey) }),
  };
}

module.exports = {
  JEV_SETTINGS_VERSION,
  applyJevSettingsPatch,
  jevSettingsPath,
  loadJevRuntimeSettings,
  publicJevConfig,
  readJevSettingsRecord,
  writeJevSettingsRecord,
};
