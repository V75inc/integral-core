'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');

const { resolveNpmInvocation } = require('./build-renderer');
const packageJson = require('../package.json');

test('runs npm-cli.js through Node on Windows npm lifecycle builds', () => {
  const invocation = resolveNpmInvocation({
    platform: 'win32',
    execPath: 'C:\\nodejs\\node.exe',
    npmExecPath: 'C:\\nodejs\\node_modules\\npm\\bin\\npm-cli.js',
  });

  assert.deepEqual(invocation, {
    file: 'C:\\nodejs\\node.exe',
    prefixArgs: ['C:\\nodejs\\node_modules\\npm\\bin\\npm-cli.js'],
    shell: false,
  });
});

test('uses the platform npm executable outside an npm lifecycle', () => {
  assert.deepEqual(
    resolveNpmInvocation({
      platform: 'linux',
      execPath: '/usr/bin/node',
      npmExecPath: '',
    }),
    {
      file: 'npm',
      prefixArgs: [],
      shell: false,
    },
  );
  assert.deepEqual(
    resolveNpmInvocation({
      platform: 'win32',
      execPath: 'C:\\nodejs\\node.exe',
      npmExecPath: '',
    }),
    {
      file: 'npm.cmd',
      prefixArgs: [],
      shell: true,
    },
  );
});

test('declares the metadata required to build Linux deb packages', () => {
  assert.match(packageJson.homepage, /^https?:\/\//);
  assert.equal(typeof packageJson.build.linux.maintainer, 'string');
  assert.notEqual(packageJson.build.linux.maintainer.trim(), '');
});
