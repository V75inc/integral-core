'use strict';

const path = require('node:path');

const desktopPackage = require('../package.json');
const { installDriver } = require('../src/driver-install');

async function main() {
  const version = desktopPackage.dependencies?.['@trycua/cua-driver'];
  if (!/^\d+\.\d+\.\d+$/.test(String(version || ''))) {
    throw new Error('@trycua/cua-driver must be pinned to an exact x.y.z version');
  }
  const installDir = path.join(__dirname, '..', 'resources', 'driver');
  const result = await installDriver({
    installDir,
    pinnedVersion: version,
    onProgress(received, total) {
      const detail = total
        ? `${Math.round((received / total) * 100)}%`
        : `${(received / 1024 / 1024).toFixed(1)} MiB`;
      process.stdout.write(`\rProvisioning Cua Driver ${version}: ${detail}`);
    },
  });
  process.stdout.write('\n');
  console.log(`${result.changed ? 'Provisioned' : 'Verified'} ${result.path}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
