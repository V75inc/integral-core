'use strict';

const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const { binaryName } = require('../src/driver-resource');
const desktopPackage = require('../package.json');

const name = binaryName();
const resource = path.join(__dirname, '..', 'resources', 'driver', name);
const digestPath = path.join(path.dirname(resource), '.installed-sha256');
const versionPath = path.join(path.dirname(resource), '.installed-version');
const pinnedVersion = desktopPackage.dependencies?.['@trycua/cua-driver'];

if (!fs.existsSync(resource) || !fs.statSync(resource).isFile()) {
  console.error(
    `Bundled Cua Driver is missing at ${resource}. Run "npm run provision:driver" before packaging.`,
  );
  process.exitCode = 1;
} else {
  const recordedDigest = fs.existsSync(digestPath)
    ? fs.readFileSync(digestPath, 'utf8').trim().toLowerCase()
    : '';
  const recordedVersion = fs.existsSync(versionPath)
    ? fs.readFileSync(versionPath, 'utf8').trim()
    : '';
  const actualDigest = crypto
    .createHash('sha256')
    .update(fs.readFileSync(resource))
    .digest('hex');
  if (
    recordedVersion !== pinnedVersion ||
    !/^[0-9a-f]{64}$/.test(recordedDigest) ||
    recordedDigest !== actualDigest
  ) {
    console.error(
      'Bundled Cua Driver does not match its pinned version and verified binary digest. Run "npm run provision:driver".',
    );
    process.exitCode = 1;
  } else if (process.platform !== 'win32' && (fs.statSync(resource).mode & 0o111) === 0) {
    console.error(`Bundled Cua Driver is not executable: ${resource}`);
    process.exitCode = 1;
  } else {
    try {
      const version = execFileSync(resource, ['--version'], {
        encoding: 'utf8',
        timeout: 10_000,
        windowsHide: true,
      });
      if (!String(version).includes(String(pinnedVersion))) {
        throw new Error(`reported ${String(version).trim()}`);
      }
    } catch (error) {
      console.error(
        `Bundled Cua Driver cannot run on this platform or reports the wrong version: ${error.message}`,
      );
      process.exitCode = 1;
    }
  }
}
