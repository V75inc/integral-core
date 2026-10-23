import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createClient, PROTOCOL } from '../dist/index.js';
function fixture(context = {}) {
  const sent = []; const listeners = new Set();
  const parent = { postMessage: value => sent.push(value) };
  const win = { parent, addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) };
  const client = createClient({ window: win, timeoutMs: 100 });
  const receive = (data, source = parent, origin = 'https://integral.test') => listeners.forEach(fn => fn({ source, origin, data: { protocol: PROTOCOL, ...data } }));
  const handshake = (extra = {}) => receive({ type: 'handshake', token: 'one', appId: 'app', workspaceId: 'ws', viewKey: 'home', context: { props: { title: 'Hello' }, ...context }, ...extra });
  handshake();
  return { client, sent, receive, handshake, listeners };
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
test('handshake, query correlation, untrusted senders, and props updates', async () => {
  const f = fixture();
  assert.equal((await f.client.ready).props.title, 'Hello');
  const pending = f.client.query('records', { limit: 5 }); await tick();
  const message = f.sent.at(-1); assert.equal(message.capabilityKey, 'records');
  f.receive({ type: 'query.result', requestId: message.requestId, ok: true, value: 'forged' }, {});
  f.receive({ type: 'read.result', requestId: message.requestId, ok: true, value: 'wrong type' });
  f.receive({ type: 'query.result', requestId: message.requestId, ok: true, value: [1] });
  assert.deepEqual(await pending, [1]);
  const updates = []; const unsubscribe = f.client.subscribe(value => updates.push(value.props.title));
  f.handshake({ context: { props: { title: 'Updated' } } });
  assert.deepEqual(updates, ['Hello', 'Updated']); unsubscribe(); f.client.dispose(); assert.equal(f.listeners.size, 0);
});
test('stable intent keys, receipt, withheld output, no automatic retries', async () => {
  const f = fixture();
  await assert.rejects(f.client.invoke('save', {}, {}), { code: 'idempotency_key_required' });
  const operation = f.client.invoke('save', {}, { idempotencyKey: 'intent-one' }); await tick();
  const message = f.sent.at(-1); assert.equal(message.idempotencyKey, 'intent-one');
  f.receive({ type: 'operation.result', requestId: message.requestId, ok: true, value: { output_disposition: 'withheld', operation_receipt: { id: 'receipt' } } });
  await assert.rejects(operation, error => error.code === 'operation_output_withheld' && error.value.operation_receipt.id === 'receipt');
  assert.equal(f.sent.filter(item => item.type === 'operation').length, 1); f.client.dispose();
});
test('operation timeout and abort report an unconfirmed outcome', async () => {
  const f = fixture();
  await assert.rejects(f.client.invoke('save', {}, { idempotencyKey: 'same-intent', timeoutMs: 5 }), { code: 'timeout', outcome: 'unconfirmed' });
  const controller = new AbortController();
  const pending = f.client.invoke('save', {}, { idempotencyKey: 'next-intent', signal: controller.signal }); await tick(); controller.abort();
  await assert.rejects(pending, { code: 'aborted', outcome: 'unconfirmed' }); f.client.dispose();
});
test('scope changes cancel pending operations, including same-token rebinds', async () => {
  const f = fixture();
  const pending = f.client.invoke('save', {}, { idempotencyKey: 'intent' }); await tick();
  f.handshake({ appId: 'other' });
  await assert.rejects(pending, { code: 'scope_changed', outcome: 'unconfirmed' }); f.client.dispose();
});
test('preview blocks operations before dispatch and disposal settles reads', async () => {
  const f = fixture({ preview: true });
  await assert.rejects(f.client.invoke('save', {}, { idempotencyKey: 'intent' }), { code: 'preview_read_only' });
  assert.equal(f.sent.filter(item => item.type === 'operation').length, 0);
  const pending = f.client.read('context'); await tick(); f.client.dispose();
  await assert.rejects(pending, { code: 'disposed' });
});
test('typed navigation is request/response and supports host error codes', async () => {
  const f = fixture(); const pending = f.client.navigate({ kind: 'entry', id: 'n.Entry.one' }); await tick();
  const message = f.sent.at(-1);
  assert.deepEqual(message.target, { kind: 'entry', id: 'n.Entry.one' });
  f.receive({ type: 'navigation.result', requestId: message.requestId, ok: false, error: 'Denied', error_code: 'access_denied' });
  await assert.rejects(pending, { code: 'access_denied' }); f.client.dispose();
});

test('cancellation settles before the initial host handshake', async () => {
  const messages = []; const listeners = new Set();
  const win = { parent: { postMessage: msg => messages.push(msg) }, addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) };
  const client = createClient({ window: win, timeoutMs: 1000 });
  const controller = new AbortController();
  const pending = client.read('context', { signal: controller.signal }); controller.abort();
  await assert.rejects(pending, error => error.code === 'aborted' && error.outcome === undefined);
  assert.equal(messages.filter(msg => msg.type === 'read').length, 0); client.dispose();
});

test('host transport uncertainty remains distinct from a rejected operation', async () => {
  const f = fixture(); const pending = f.client.invoke('save', {}, { idempotencyKey: 'intent' }); await tick();
  f.receive({ type: 'operation.result', requestId: f.sent.at(-1).requestId, ok: false, error_code: 'operation_unconfirmed', outcome: 'unconfirmed' });
  await assert.rejects(pending, { code: 'operation_unconfirmed', outcome: 'unconfirmed' }); f.client.dispose();
});
