'use strict';

const assert = require('node:assert/strict');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { ComputerUseBroker } = require('../src/computer-use-broker');

function approvedGrant(overrides = {}) {
  return {
    id: 'grant-1',
    principalId: 'user-1',
    workspaceId: 'workspace-1',
    bindingGeneration: 'binding-1',
    approvedLocally: true,
    approvedAt: '2026-09-30T12:00:00.000Z',
    expiresAt: '2026-09-30T13:00:00.000Z',
    idleTimeoutSeconds: 300,
    screenshotsAllowed: true,
    apps: [{ pid: 42, name: 'Editor', bundleId: 'com.example.Editor' }],
    ...overrides,
  };
}

function fakeSdk() {
  const calls = {
    workerOptions: [],
    listApps: 0,
    listWindows: [],
    getWindowState: [],
    click: [],
    typeText: [],
    pressKey: [],
    hotkey: [],
    shutdown: 0,
    destroyed: 0,
  };
  const driver = {
    async listApps() {
      calls.listApps += 1;
      return { apps: [{ pid: 42, name: 'Editor', running: true }] };
    },
    async listWindows(input) {
      calls.listWindows.push(input);
      return { windows: [{ pid: input.pid, windowId: 9007199254740993n, title: 'Document' }] };
    },
    async getWindowState(input) {
      calls.getWindowState.push(input);
      if (input.screenshotOutFile) {
        await fsp.writeFile(input.screenshotOutFile, Buffer.from('png bytes'));
      }
      return {
        pid: input.pid,
        windowId: input.windowId,
        snapshotId: 'snapshot-1',
        treeMarkdown: '- button "Save"',
        elements: [
          { elementIndex: 1n, role: 'button', depth: 1, elementToken: 'token-save', label: 'Save' },
        ],
        screenshotFilePath: input.screenshotOutFile,
        screenshotMimeType: 'image/png',
        images: [{ mimeType: 'image/png', dataBase64: 'must-not-cross-control-channel' }],
      };
    },
    async click(input) {
      calls.click.push(input);
      return { effect: 0, route: 0, summary: 'clicked' };
    },
    async typeText(input) {
      calls.typeText.push(input);
      return { text: 'typed' };
    },
    async pressKey(input) {
      calls.pressKey.push(input);
      return { text: 'pressed' };
    },
    async hotkey(input) {
      calls.hotkey.push(input);
      return { text: 'hotkey' };
    },
    async shutdown() {
      calls.shutdown += 1;
    },
    uniffiDestroy() {
      calls.destroyed += 1;
    },
  };
  return {
    calls,
    driver,
    module: {
      SessionPermissionMode: { Bounded: 7 },
      CuaDriver: {
        createPrivateWorker(options) {
          calls.workerOptions.push(options);
          return driver;
        },
      },
    },
  };
}

test('an approved grant starts one bounded private worker and serves curated app discovery', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  const now = () => new Date('2026-09-30T12:10:00.000Z');
  const broker = new ComputerUseBroker({
    binaryPath: '/Applications/Integral.app/Contents/Resources/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now,
  });

  const activation = await broker.activate(approvedGrant());
  const result = await broker.invoke({
    operation: 'driver__list_apps',
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
    arguments: {},
  });

  assert.equal(activation.runtimeGeneration, 1);
  assert.deepEqual(result, {
    runtimeGeneration: 1,
    data: {
      apps: [{
        pid: 42,
        name: 'Editor',
        running: true,
        bundleId: 'com.example.Editor',
      }],
    },
    artifacts: [],
  });
  assert.equal(sdk.calls.listApps, 0);
  assert.equal(sdk.calls.workerOptions.length, 1);
  const worker = sdk.calls.workerOptions[0];
  assert.equal(worker.binaryPath, broker.binaryPath);
  assert.equal(worker.hostBundleId, 'ai.integral.desktop');
  assert.equal(worker.inheritStderr, false);
  assert.equal(worker.configuredDriver.authorization.compatibilityMode, 7);
  assert.deepEqual(worker.configuredDriver.authorization.allowedModes, [7]);

  const manifest = JSON.parse(await fsp.readFile(worker.configuredDriver.authorization.compatibilityCapabilityManifestPath, 'utf8'));
  assert.deepEqual(manifest.allow.tools, ['list_apps', 'list_windows', 'get_window_state']);
  assert.deepEqual(manifest.resources, {
    apps: [{ bundle_id: 'com.example.Editor', windows: 'all', launch: false, terminate: 'deny' }],
    desktop: { display: false },
  });

  await broker.stop();
  assert.equal(sdk.calls.shutdown, 1);
  assert.equal(sdk.calls.destroyed, 1);
});

test('window discovery and snapshots stay typed while screenshot bytes become a bounded artifact', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
    maxArtifactBytes: 1024,
  });
  await broker.activate(approvedGrant());
  const base = {
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
  };

  const windows = await broker.invoke({
    ...base,
    operation: 'driver__list_windows',
    arguments: { pid: 42 },
  });
  const snapshot = await broker.invoke({
    ...base,
    operation: 'driver__snapshot_window',
    arguments: { pid: 42, window_id: '9007199254740993', max_dimension: 1280 },
  });

  assert.deepEqual(sdk.calls.listWindows, [{ pid: 42, onScreenOnly: true }]);
  assert.deepEqual(windows.data, {
    windows: [{ pid: 42, windowId: '9007199254740993', title: 'Document' }],
  });
  assert.equal(sdk.calls.getWindowState[0].windowId, 9007199254740993n);
  assert.equal(sdk.calls.getWindowState[0].includeScreenshot, true);
  assert.equal(sdk.calls.getWindowState[0].maxImageDimension, 1280);
  assert.deepEqual(snapshot.data, {
    pid: 42,
    windowId: '9007199254740993',
    snapshotId: 'snapshot-1',
    treeMarkdown: '- button "Save"',
    elements: [
      { elementIndex: '1', role: 'button', depth: 1, elementToken: 'token-save', label: 'Save' },
    ],
    screenshotMimeType: 'image/png',
    images: [],
  });
  assert.equal(snapshot.artifacts.length, 1);
  assert.equal(snapshot.artifacts[0].contentType, 'image/png');
  assert.equal(snapshot.artifacts[0].bytes, Buffer.byteLength('png bytes'));
  assert.match(snapshot.artifacts[0].sha256, /^[0-9a-f]{64}$/);
  assert.equal((await fsp.readFile(snapshot.artifacts[0].path)).toString(), 'png bytes');

  await broker.releaseArtifact(snapshot.artifacts[0].id);
  await assert.rejects(fsp.stat(snapshot.artifacts[0].path));
  await broker.stop();
});

test('local app discovery uses an isolated list-only worker and leaves no active authority', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });

  const result = await broker.discoverApps();

  assert.deepEqual(result, { apps: [{ pid: 42, name: 'Editor', running: true }] });
  assert.equal(sdk.calls.workerOptions.length, 1);
  const manifestPath = sdk.calls.workerOptions[0].configuredDriver.authorization
    .compatibilityCapabilityManifestPath;
  const manifest = JSON.parse(await fsp.readFile(manifestPath, 'utf8').catch(() => '{}'));
  assert.deepEqual(manifest, {}, 'the discovery manifest is removed after the worker stops');
  assert.equal(sdk.calls.shutdown, 1);
  assert.equal(sdk.calls.destroyed, 1);
  await assert.rejects(
    broker.invoke({
      operation: 'driver__list_apps',
      bindingGeneration: 'binding-1',
      deadline: '2026-09-30T12:11:00.000Z',
      arguments: {},
    }),
    (error) => error.code === 'computer_use.not_active',
  );
});

test('local grant expiry tears down the worker and revokes advertised authority', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  let revoked;
  const revokedPromise = new Promise(resolve => {
    revoked = resolve;
  });
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    onRevoked: revoked,
  });
  const now = new Date();
  await broker.activate(approvedGrant({
    approvedAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + 20).toISOString(),
  }));
  assert.equal(broker.active, true);

  const event = await revokedPromise;

  assert.equal(event.reason, 'expired');
  assert.equal(broker.active, false);
  assert.equal(sdk.calls.shutdown, 1);
});

test('a read admitted before revoke cannot return data afterward', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  let finishRead;
  sdk.driver.listWindows = () => new Promise(resolve => {
    finishRead = resolve;
  });
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
  });
  const now = new Date();
  await broker.activate(approvedGrant({
    approvedAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + 60_000).toISOString(),
  }));
  const pending = broker.invoke({
    operation: 'driver__list_windows',
    bindingGeneration: 'binding-1',
    deadline: new Date(Date.now() + 30_000).toISOString(),
    arguments: { pid: 42 },
  });
  await Promise.resolve();
  await broker.stop();
  finishRead({ windows: [{ pid: 42, windowId: 7n }] });

  await assert.rejects(
    pending,
    (error) => error.code === 'computer_use.revoked_during_call',
  );
});

test('an action lease advertises click tools and requires a fresh snapshot token', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });
  await broker.activate(approvedGrant({ actionsAllowed: true }));
  const base = {
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
  };

  assert.deepEqual(
    broker.advertisedCapabilities(),
    [
      'driver__list_apps',
      'driver__list_windows',
      'driver__snapshot_window',
      'driver__grant_state',
      'driver__act',
    ],
  );
  const manifest = JSON.parse(await fsp.readFile(
    sdk.calls.workerOptions[0].configuredDriver.authorization.compatibilityCapabilityManifestPath,
    'utf8',
  ));
  assert.ok(manifest.allow.tools.includes('click'));
  assert.ok(manifest.allow.tools.includes('type_text'));

  await assert.rejects(
    broker.invoke({
      ...base,
      callId: 'act-1',
      operation: 'driver__act',
      arguments: {
        action: 'click',
        pid: 42,
        window_id: '9007199254740993',
        snapshot_id: 'snapshot-1',
        element_token: 'token-save',
      },
    }),
    (error) => error.code === 'computer_use.snapshot_stale',
  );

  await broker.invoke({
    ...base,
    operation: 'driver__snapshot_window',
    arguments: { pid: 42, window_id: '9007199254740993' },
  });
  const acted = await broker.invoke({
    ...base,
    callId: 'act-2',
    operation: 'driver__act',
    arguments: {
      action: 'type_text',
      pid: 42,
      window_id: '9007199254740993',
      snapshot_id: 'snapshot-1',
      element_token: 'token-save',
      text: '7',
    },
  });

  assert.equal(sdk.calls.click.length, 1);
  assert.equal(sdk.calls.typeText.length, 1);
  assert.equal(sdk.calls.typeText[0].text, '7');
  assert.equal(acted.data.outcome, 'completed');
  assert.equal(acted.data.foreground_used, false);
  assert.equal(acted.data.verification.snapshotId, 'snapshot-1');
  assert.equal(acted.artifacts.length, 0);
  assert.equal(
    sdk.calls.getWindowState[sdk.calls.getWindowState.length - 1].includeScreenshot,
    false,
  );
  await broker.stop();
});

test('a same-snapshot action burst verifies once after every step', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });
  await broker.activate(approvedGrant({ actionsAllowed: true }));
  const base = {
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
  };
  await broker.invoke({
    ...base,
    operation: 'driver__snapshot_window',
    arguments: { pid: 42, window_id: '9007199254740993' },
  });
  const before = sdk.calls.getWindowState.length;
  const acted = await broker.invoke({
    ...base,
    callId: 'act-burst',
    operation: 'driver__act',
    arguments: {
      pid: 42,
      window_id: '9007199254740993',
      snapshot_id: 'snapshot-1',
      element_token: 'token-save',
      steps: [
        { action: 'press_key', key: '1' },
        { action: 'press_key', key: '2' },
        { action: 'press_key', key: '3' },
        { action: 'press_key', key: '4' },
        { action: 'press_key', key: '5' },
      ],
    },
  });

  assert.equal(sdk.calls.pressKey.length, 5);
  assert.equal(sdk.calls.click.length, 1);
  assert.equal(sdk.calls.getWindowState.length, before + 1);
  assert.equal(
    sdk.calls.getWindowState[sdk.calls.getWindowState.length - 1].includeScreenshot,
    false,
  );
  assert.equal(acted.artifacts.length, 0);
  assert.deepEqual(acted.data.steps, [
    'press_key', 'press_key', 'press_key', 'press_key', 'press_key',
  ]);
  assert.equal(acted.data.outcome, 'completed');
  await broker.stop();
});

test('an explicit verify_screenshot still captures a post-act image', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });
  await broker.activate(approvedGrant({ actionsAllowed: true }));
  const base = {
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
  };
  await broker.invoke({
    ...base,
    operation: 'driver__snapshot_window',
    arguments: { pid: 42, window_id: '9007199254740993' },
  });
  const acted = await broker.invoke({
    ...base,
    callId: 'act-verify-shot',
    operation: 'driver__act',
    arguments: {
      action: 'type_text',
      pid: 42,
      window_id: '9007199254740993',
      snapshot_id: 'snapshot-1',
      element_token: 'token-save',
      text: '7',
      verify_screenshot: true,
    },
  });
  assert.equal(acted.artifacts.length, 1);
  assert.equal(
    sdk.calls.getWindowState[sdk.calls.getWindowState.length - 1].includeScreenshot,
    true,
  );
  await broker.stop();
});

test('an unknown action outcome cannot be retried and observation grants cannot click', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  sdk.driver.click = async () => {
    throw new Error('worker died');
  };
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });
  await broker.activate(approvedGrant());
  await assert.rejects(
    broker.invoke({
      bindingGeneration: 'binding-1',
      deadline: '2026-09-30T12:11:00.000Z',
      callId: 'act-observe',
      operation: 'driver__act',
      arguments: {
        action: 'click',
        pid: 42,
        window_id: '7',
        snapshot_id: 'snapshot-1',
        element_token: 'token-save',
      },
    }),
    (error) => error.code === 'computer_use.actions_not_consented',
  );
  await broker.stop();

  await broker.activate(approvedGrant({ actionsAllowed: true }));
  await broker.invoke({
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
    operation: 'driver__snapshot_window',
    arguments: { pid: 42, window_id: '9007199254740993' },
  });
  await assert.rejects(
    broker.invoke({
      bindingGeneration: 'binding-1',
      deadline: '2026-09-30T12:11:00.000Z',
      callId: 'act-unknown',
      operation: 'driver__act',
      arguments: {
        action: 'click',
        pid: 42,
        window_id: '9007199254740993',
        snapshot_id: 'snapshot-1',
        element_token: 'token-save',
      },
    }),
    (error) => error.code === 'computer_use.unknown_outcome',
  );
  await assert.rejects(
    broker.invoke({
      bindingGeneration: 'binding-1',
      deadline: '2026-09-30T12:11:00.000Z',
      callId: 'act-unknown',
      operation: 'driver__act',
      arguments: {
        action: 'click',
        pid: 42,
        window_id: '9007199254740993',
        snapshot_id: 'snapshot-1',
        element_token: 'token-save',
      },
    }),
    (error) => error.code === 'computer_use.unknown_outcome',
  );
  await broker.stop();
});

test('a screenshot capture failure still returns the accessibility tree', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  sdk.driver.getWindowState = async (input) => {
    sdk.calls.getWindowState.push(input);
    if (input.includeScreenshot) {
      throw new Error('CGWindow capture failed for untitled window');
    }
    return {
      pid: input.pid,
      windowId: input.windowId,
      snapshotId: 'snapshot-ax',
      treeMarkdown: '- staticText "42"',
      elements: [
        { elementIndex: 1n, role: 'staticText', depth: 1, elementToken: 'token-display', value: '42' },
      ],
      images: [],
    };
  };
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });
  await broker.activate(approvedGrant());
  const snapshot = await broker.invoke({
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
    operation: 'driver__snapshot_window',
    arguments: { pid: 42, window_id: '62' },
  });

  assert.equal(sdk.calls.getWindowState.length, 2);
  assert.equal(sdk.calls.getWindowState[0].includeScreenshot, true);
  assert.equal(sdk.calls.getWindowState[1].includeScreenshot, false);
  assert.equal(snapshot.data.snapshotId, 'snapshot-ax');
  assert.equal(snapshot.data.screenshot_omitted, true);
  assert.match(snapshot.data.screenshot_omitted_reason, /CGWindow capture failed/);
  assert.equal(snapshot.data.elements[0].value, '42');
  assert.equal(snapshot.artifacts.length, 0);
  await broker.stop();
});

test('an unresolved AX window retries a screenshot-only capture', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  sdk.driver.getWindowState = async (input) => {
    sdk.calls.getWindowState.push(input);
    if (input.includeAccessibilityTree !== false) {
      const error = new Error(
        'ax_window_unresolved: no AXWindow under that pid reports this window ID',
      );
      error.code = 'ax_window_unresolved';
      throw error;
    }
    if (input.screenshotOutFile) {
      await fsp.writeFile(input.screenshotOutFile, Buffer.from('png bytes'));
    }
    return {
      pid: input.pid,
      windowId: input.windowId,
      snapshotId: 'snapshot-pixels',
      treeMarkdown: '',
      elements: [],
      screenshotFilePath: input.screenshotOutFile,
      screenshotMimeType: 'image/png',
      images: [],
    };
  };
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });
  await broker.activate(approvedGrant());
  const snapshot = await broker.invoke({
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
    operation: 'driver__snapshot_window',
    arguments: { pid: 764, window_id: '62' },
  });

  assert.equal(sdk.calls.getWindowState.length, 2);
  assert.equal(sdk.calls.getWindowState[0].includeAccessibilityTree, true);
  assert.equal(sdk.calls.getWindowState[1].includeAccessibilityTree, false);
  assert.equal(sdk.calls.getWindowState[1].includeScreenshot, true);
  assert.equal(snapshot.data.snapshotId, 'snapshot-pixels');
  assert.equal(snapshot.artifacts.length, 1);
  await broker.stop();
});

test('an accessibility snapshot failure is classified instead of a generic adapter error', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  sdk.driver.getWindowState = async () => {
    throw new Error('window 62 is not snapshotable');
  };
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
  });
  await broker.activate(approvedGrant());
  await assert.rejects(
    broker.invoke({
      bindingGeneration: 'binding-1',
      deadline: '2026-09-30T12:11:00.000Z',
      operation: 'driver__snapshot_window',
      arguments: { pid: 42, window_id: '62' },
    }),
    (error) =>
      error.code === 'computer_use.snapshot_failed' &&
      error.message.includes('not snapshotable'),
  );
  await broker.stop();
});

test('Jev choose maps an offered AX candidate to a local element token', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-computer-use-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  const sdk = fakeSdk();
  const broker = new ComputerUseBroker({
    binaryPath: '/opt/integral/driver/cua-driver',
    hostBundleId: 'ai.integral.desktop',
    stateDir: root,
    loadSdk: async () => sdk.module,
    now: () => new Date('2026-09-30T12:10:00.000Z'),
    jev: {
      getSettings: () => ({ enabled: true, apiKey: 'sk-test' }),
      choose: async ({ candidates }) => ({
        selectedId: candidates[0].id,
        confidence: 1,
        model: 'mock',
      }),
    },
  });
  await broker.activate(approvedGrant({ actionsAllowed: true }));
  const base = {
    bindingGeneration: 'binding-1',
    deadline: '2026-09-30T12:11:00.000Z',
  };
  assert.ok(broker.advertisedCapabilities().includes('driver__choose'));
  await broker.invoke({
    ...base,
    operation: 'driver__snapshot_window',
    arguments: { pid: 42, window_id: '9007199254740993' },
  });
  const chosen = await broker.invoke({
    ...base,
    operation: 'driver__choose',
    arguments: {
      pid: 42,
      window_id: '9007199254740993',
      snapshot_id: 'snapshot-1',
      goal: 'Save the document',
    },
  });
  assert.equal(chosen.data.outcome, 'act');
  assert.equal(chosen.data.action, 'click');
  assert.equal(chosen.data.element_token, 'token-save');
  await broker.stop();
});
