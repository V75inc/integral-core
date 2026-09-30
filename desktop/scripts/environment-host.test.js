'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { DesktopEnvironmentHost } = require('../src/environment-host');

function hostFor(rootPath) {
  let settings = {
    desktopEnvironmentEnabled: true,
    desktopEnvironmentDeviceId: 'device_test',
    desktopEnvironmentRoots: [{ id: 'root-1', label: 'Test', path: rootPath }],
  };
  return new DesktopEnvironmentHost({
    readSettings: () => settings,
    writeSettings: (patch) => {
      settings = { ...settings, ...patch };
    },
    getApiUrl: () => 'http://localhost:4000',
    notifyDisconnected: () => {},
  });
}

test('reads only below an explicitly granted root', async (t) => {
  const temp = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-desktop-'));
  t.after(() => fsp.rm(temp, { recursive: true, force: true }));
  await fsp.writeFile(path.join(temp, 'note.txt'), 'hello desktop', 'utf8');
  const host = hostFor(temp);

  const result = await host.invoke('desktop__read_file', {
    root_id: 'root-1',
    path: 'note.txt',
  });

  assert.equal(result.content, 'hello desktop');
  await assert.rejects(
    host.invoke('desktop__read_file', {
      root_id: 'root-1',
      path: '../outside.txt',
    }),
    (error) => error.code === 'environment.path_outside_grant',
  );
});

test('rejects a symlink that resolves outside the granted root', async (t) => {
  const temp = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-desktop-'));
  const outside = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-outside-'));
  t.after(() => fsp.rm(temp, { recursive: true, force: true }));
  t.after(() => fsp.rm(outside, { recursive: true, force: true }));
  await fsp.writeFile(path.join(outside, 'secret.txt'), 'secret', 'utf8');
  try {
    fs.symlinkSync(outside, path.join(temp, 'escape'), 'dir');
  } catch (error) {
    t.skip(`symlinks unavailable: ${error.message}`);
    return;
  }
  const host = hostFor(temp);

  await assert.rejects(
    host.invoke('desktop__read_file', {
      root_id: 'root-1',
      path: 'escape/secret.txt',
    }),
    (error) => error.code === 'environment.path_outside_grant',
  );
});

test('grep returns bounded relative-path matches', async (t) => {
  const temp = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-desktop-'));
  t.after(() => fsp.rm(temp, { recursive: true, force: true }));
  await fsp.mkdir(path.join(temp, 'nested'));
  await fsp.writeFile(path.join(temp, 'nested', 'a.txt'), 'one\nNeedle\nthree', 'utf8');
  const host = hostFor(temp);

  const result = await host.invoke('desktop__grep', {
    root_id: 'root-1',
    pattern: 'needle',
  });

  assert.deepEqual(result.matches, [
    { path: 'nested/a.txt', line: 2, text: 'Needle' },
  ]);
});

test('connect resolves only after the backend ready handshake', async (t) => {
  const originalWebSocket = global.WebSocket;
  let socket;
  let readyNotifications = 0;
  class FakeWebSocket {
    static CLOSING = 2;

    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.listeners = new Map();
      socket = this;
    }

    addEventListener(type, callback) {
      this.listeners.set(type, callback);
    }

    emit(type, data) {
      this.listeners.get(type)?.({ data });
    }

    close() {
      this.readyState = 3;
    }

    send() {}
  }
  global.WebSocket = FakeWebSocket;
  t.after(() => {
    global.WebSocket = originalWebSocket;
  });
  const host = new DesktopEnvironmentHost({
    readSettings: () => ({
      desktopEnvironmentEnabled: true,
      desktopEnvironmentDeviceId: 'device_test',
      desktopEnvironmentRoots: [],
    }),
    writeSettings: () => {},
    getApiUrl: () => 'http://localhost:4000',
    notifyReady: () => {
      readyNotifications += 1;
    },
    notifyDisconnected: () => {},
  });

  let resolved = false;
  const connection = host
    .connect({ ticket: 'one-use', websocket_path: '/ws/desktop-environment' })
    .then(() => {
      resolved = true;
    });
  await Promise.resolve();
  assert.equal(resolved, false);
  socket.emit(
    'message',
    JSON.stringify({ type: 'ready', binding_id: 'live-binding' }),
  );
  await connection;

  assert.equal(host.bindingId, 'live-binding');
  assert.equal(readyNotifications, 1);
  assert.match(socket.url, /ticket=one-use$/);
});

test('driver calls use narrow control results and binary artifact frames', async (t) => {
  const temp = await fsp.mkdtemp(path.join(os.tmpdir(), 'integral-driver-rpc-'));
  t.after(() => fsp.rm(temp, { recursive: true, force: true }));
  const screenshot = path.join(temp, 'snapshot.png');
  await fsp.writeFile(screenshot, Buffer.from('image-bytes'));
  const released = [];
  const broker = {
    async invoke(call) {
      assert.equal(call.operation, 'driver__snapshot_window');
      return {
        runtimeGeneration: 4,
        data: { snapshotId: 'snapshot-1', treeMarkdown: '- button "Save"' },
        artifacts: [{
          id: 'artifact-1',
          path: screenshot,
          contentType: 'image/png',
          bytes: 11,
          sha256: 'a'.repeat(64),
        }],
      };
    },
    async releaseArtifact(id) {
      released.push(id);
    },
    isGenerationActive(generation) {
      return generation === 4;
    },
  };
  const host = new DesktopEnvironmentHost({
    readSettings: () => ({
      desktopEnvironmentEnabled: true,
      desktopEnvironmentDeviceId: 'device_test',
      desktopEnvironmentRoots: [],
    }),
    writeSettings: () => {},
    getApiUrl: () => 'http://localhost:4000',
    notifyDisconnected: () => {},
    computerUseBroker: broker,
  });
  host.bindingId = 'binding-1';
  const sent = [];
  const socket = { send: (message) => sent.push(message) };

  await host.onMessage(socket, JSON.stringify({
    type: 'driver_call',
    id: 'call-1',
    binding_generation: 'binding-1',
    runtime_generation: 4,
    operation: 'driver__snapshot_window',
    deadline: '2026-09-30T12:11:00.000Z',
    arguments: { pid: 42, window_id: '7' },
  }));

  assert.equal(Buffer.isBuffer(sent[0]), true);
  const headerLength = sent[0].readUInt32BE(0);
  const header = JSON.parse(sent[0].subarray(4, 4 + headerLength).toString('utf8'));
  assert.deepEqual(header, {
    type: 'driver_artifact_chunk',
    call_id: 'call-1',
    artifact_id: 'artifact-1',
    binding_generation: 'binding-1',
    runtime_generation: 4,
    sequence: 0,
    final: true,
    content_type: 'image/png',
    total_bytes: 11,
    sha256: 'a'.repeat(64),
  });
  assert.equal(sent[0].subarray(4 + headerLength).toString(), 'image-bytes');
  assert.deepEqual(JSON.parse(sent[1]), {
    type: 'driver_result',
    id: 'call-1',
    ok: true,
    runtime_generation: 4,
    content_untrusted: true,
    data: { snapshotId: 'snapshot-1', treeMarkdown: '- button "Save"' },
    artifacts: [{
      id: 'artifact-1',
      content_type: 'image/png',
      bytes: 11,
      sha256: 'a'.repeat(64),
    }],
  });
  assert.deepEqual(released, ['artifact-1']);
});
