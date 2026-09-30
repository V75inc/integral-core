'use strict';

const fs = require('node:fs');
const path = require('node:path');

const GRANT_MEMORY_VERSION = 1;
const MAX_REMEMBERED_APPS = 10;

function grantMemoryPath(userDataDir) {
  return path.join(userDataDir, 'computer-use-grant.json');
}

function persistableApp(app) {
  if (!app || typeof app !== 'object') return null;
  const name = String(app.name || '').trim().slice(0, 120);
  const bundleId = String(app.bundleId || '').trim();
  const executable = String(app.executable || '').trim();
  if (!name) return null;
  if (bundleId) return { name, bundleId };
  if (executable) return { name, executable };
  return null;
}

function normalizeGrantMemory(raw) {
  if (!raw || typeof raw !== 'object' || raw.version !== GRANT_MEMORY_VERSION) {
    return null;
  }
  const apps = (Array.isArray(raw.apps) ? raw.apps : [])
    .map(persistableApp)
    .filter(Boolean)
    .slice(0, MAX_REMEMBERED_APPS);
  const durationMinutes = Number(raw.durationMinutes);
  if (apps.length === 0 || !Number.isFinite(durationMinutes)) return null;
  return {
    version: GRANT_MEMORY_VERSION,
    grantId: raw.grantId ? String(raw.grantId) : null,
    approvedAt: raw.approvedAt ? String(raw.approvedAt) : null,
    expiresAt: raw.expiresAt ? String(raw.expiresAt) : null,
    durationMinutes: Math.min(480, Math.max(5, Math.round(durationMinutes))),
    screenshotsAllowed: raw.screenshotsAllowed === true,
    actionsAllowed: raw.actionsAllowed === true,
    revoked: raw.revoked === true,
    apps,
  };
}

function readGrantMemory(filePath) {
  try {
    return normalizeGrantMemory(JSON.parse(fs.readFileSync(filePath, 'utf8')));
  } catch {
    return null;
  }
}

function writeGrantMemory(filePath, record) {
  const normalized = normalizeGrantMemory(record);
  if (!normalized) {
    throw new Error('Computer-use grant memory is incomplete');
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  fs.writeFileSync(filePath, `${JSON.stringify(normalized, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
  });
  return normalized;
}

function rememberApprovedGrant(filePath, grant, durationMinutes) {
  return writeGrantMemory(filePath, {
    version: GRANT_MEMORY_VERSION,
    grantId: grant.id,
    approvedAt: grant.approvedAt,
    expiresAt: grant.expiresAt,
    durationMinutes,
    screenshotsAllowed: grant.screenshotsAllowed === true,
    actionsAllowed: grant.actionsAllowed === true,
    revoked: false,
    apps: grant.apps,
  });
}

function rememberRevokedGrant(filePath) {
  const current = readGrantMemory(filePath);
  if (!current) return null;
  return writeGrantMemory(filePath, {
    ...current,
    expiresAt: null,
    revoked: true,
  });
}

function rememberExpiredGrant(filePath, at = new Date()) {
  const current = readGrantMemory(filePath);
  if (!current) return null;
  return writeGrantMemory(filePath, {
    ...current,
    expiresAt: at.toISOString(),
    revoked: false,
  });
}

function isLiveGrantMemory(record, now = new Date()) {
  if (!record || record.revoked === true || !record.expiresAt) return false;
  const expiresAt = new Date(record.expiresAt);
  return Number.isFinite(expiresAt.getTime()) && expiresAt > now;
}

function remainingGrantSeconds(record, now = new Date()) {
  if (!isLiveGrantMemory(record, now)) return 0;
  return Math.max(1, Math.floor((new Date(record.expiresAt).getTime() - now.getTime()) / 1000));
}

function grantFromMemory(record, bindingGeneration, now = new Date()) {
  if (!isLiveGrantMemory(record, now)) return null;
  return {
    id: record.grantId || cryptoRandomId(),
    principalId: '',
    workspaceId: '',
    bindingGeneration,
    approvedLocally: true,
    approvedAt: record.approvedAt || now.toISOString(),
    expiresAt: record.expiresAt,
    idleTimeoutSeconds: Math.min(1800, remainingGrantSeconds(record, now)),
    screenshotsAllowed: record.screenshotsAllowed === true,
    actionsAllowed: record.actionsAllowed === true,
    apps: record.apps,
  };
}

function cryptoRandomId() {
  return require('node:crypto').randomUUID();
}

function publicGrantConfig(record, activeGrant) {
  const source = activeGrant
    ? {
        grantId: activeGrant.id,
        expiresAt: activeGrant.expiresAt,
        durationMinutes: record?.durationMinutes ?? null,
        screenshotsAllowed: activeGrant.screenshotsAllowed === true,
        actionsAllowed: activeGrant.actionsAllowed === true,
        apps: (activeGrant.apps || []).map(persistableApp).filter(Boolean),
      }
    : record
      ? {
          grantId: record.grantId,
          expiresAt: isLiveGrantMemory(record) ? record.expiresAt : null,
          durationMinutes: record.durationMinutes,
          screenshotsAllowed: record.screenshotsAllowed,
          actionsAllowed: record.actionsAllowed,
          apps: record.apps,
        }
      : {
          grantId: null,
          expiresAt: null,
          durationMinutes: null,
          screenshotsAllowed: false,
          actionsAllowed: false,
          apps: [],
        };
  return {
    grantId: source.grantId,
    expiresAt: source.expiresAt,
    durationMinutes: source.durationMinutes,
    screenshotsAllowed: source.screenshotsAllowed,
    actionsAllowed: source.actionsAllowed,
    apps: source.apps,
  };
}

module.exports = {
  GRANT_MEMORY_VERSION,
  grantFromMemory,
  grantMemoryPath,
  isLiveGrantMemory,
  persistableApp,
  publicGrantConfig,
  readGrantMemory,
  remainingGrantSeconds,
  rememberApprovedGrant,
  rememberExpiredGrant,
  rememberRevokedGrant,
  writeGrantMemory,
};
