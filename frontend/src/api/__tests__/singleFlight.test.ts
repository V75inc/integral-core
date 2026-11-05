import { describe, expect, it, vi } from 'vitest';
import { clearAllSingleFlights, createSingleFlight } from '../singleFlight';

describe('createSingleFlight', () => {
  it('shares one call between concurrent callers', async () => {
    const sf = createSingleFlight<number>(1000);
    const fn = vi.fn().mockResolvedValue(1);
    const r = await Promise.all([sf.run('k', fn), sf.run('k', fn), sf.run('k', fn)]);
    expect(r).toEqual([1, 1, 1]);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('keeps keys separate', async () => {
    const sf = createSingleFlight<number>(1000);
    const fn = vi.fn().mockResolvedValue(1);
    await Promise.all([sf.run('a', fn), sf.run('b', fn)]);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('does not cache failures', async () => {
    const sf = createSingleFlight<number>(1000);
    const fn = vi.fn().mockRejectedValueOnce(new Error('x')).mockResolvedValue(2);
    await expect(sf.run('k', fn)).rejects.toThrow('x');
    await expect(sf.run('k', fn)).resolves.toBe(2);
  });

  it('expires after the ttl', async () => {
    const sf = createSingleFlight<number>(10);
    const fn = vi.fn().mockResolvedValue(1);
    await sf.run('k', fn);
    await new Promise(r => setTimeout(r, 25));
    await sf.run('k', fn);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('is dropped by clearAllSingleFlights (a write happened)', async () => {
    const sf = createSingleFlight<number>(1000);
    const fn = vi.fn().mockResolvedValue(1);
    await sf.run('k', fn);
    clearAllSingleFlights();
    await sf.run('k', fn);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

import axios, { type AxiosAdapter } from 'axios';
import { dedupeConcurrentGets } from '../singleFlight';

describe('dedupeConcurrentGets', () => {
  function makeClient() {
    let calls = 0;
    const pending: Array<() => void> = [];
    const gate = { release: () => { pending.splice(0).forEach(fn => fn()); } };
    const adapter: AxiosAdapter = config => {
      calls += 1;
      const n = calls;
      return new Promise(resolve => {
        pending.push(() =>
          resolve({ data: { n, url: config.url }, status: 200, statusText: 'OK', headers: {}, config }),
        );
      });
    };
    const client = axios.create({ adapter });
    dedupeConcurrentGets(client);
    return { client, gate, calls: () => calls };
  }

  it('shares overlapping identical GETs and gives each caller its own data', async () => {
    const { client, gate, calls } = makeClient();
    const a = client.get('/x', { params: { q: 1 } });
    const b = client.get('/x', { params: { q: 1 } });
    gate.release();
    const [ra, rb] = await Promise.all([a, b]);
    expect(calls()).toBe(1);
    expect(ra.data).toEqual(rb.data);
    expect(ra.data).not.toBe(rb.data);
  });

  it('does not share different URLs, params or non-GETs, and never serves a settled call', async () => {
    const { client, gate, calls } = makeClient();
    const reqs = [client.get('/x'), client.get('/y'), client.get('/x', { params: { q: 2 } }), client.post('/x', {})];
    gate.release();
    await Promise.all(reqs);
    expect(calls()).toBe(4);
    const later = client.get('/x');
    gate.release();
    await later;
    expect(calls()).toBe(5);
  });

  it('leaves aborted-capable requests alone', async () => {
    const { client, gate, calls } = makeClient();
    const ac = new AbortController();
    const p = [client.get('/x', { signal: ac.signal }), client.get('/x', { signal: ac.signal })];
    gate.release();
    await Promise.all(p);
    expect(calls()).toBe(2);
  });
});
