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
