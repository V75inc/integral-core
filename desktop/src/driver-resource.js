'use strict';

const path = require('node:path');

function binaryName(platform = process.platform) {
  return platform === 'win32' ? 'cua-driver.exe' : 'cua-driver';
}

function bundledDriverPath({
  resourcesPath = process.resourcesPath || '',
  appRoot = path.join(__dirname, '..'),
  packaged = false,
  platform = process.platform,
} = {}) {
  const resourceRoot = packaged
    ? resourcesPath
    : path.join(appRoot, 'resources');
  return path.resolve(resourceRoot, 'driver', binaryName(platform));
}

module.exports = { binaryName, bundledDriverPath };
