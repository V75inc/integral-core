'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const { binaryName, bundledDriverPath } = require('../src/driver-resource');

test('bundled runtime paths never fall back to PATH or a user installation', () => {
  assert.equal(binaryName('win32'), 'cua-driver.exe');
  assert.equal(binaryName('darwin'), 'cua-driver');
  assert.equal(
    bundledDriverPath({
      resourcesPath: '/Applications/Integral.app/Contents/Resources',
      packaged: true,
      platform: 'darwin',
    }),
    '/Applications/Integral.app/Contents/Resources/driver/cua-driver',
  );
  assert.equal(
    bundledDriverPath({
      appRoot: '/repo/desktop',
      packaged: false,
      platform: 'win32',
    }),
    path.resolve('/repo/desktop/resources/driver/cua-driver.exe'),
  );
});
