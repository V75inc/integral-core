import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearTokens, getAccessToken, getRefreshToken, pauseForSessionRenewal, refreshAccessToken, setTokens } from './session';
import { enableSessionRecovery, getSessionRecovery } from './sessionRecovery';

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  enableSessionRecovery('owner');
});
afterEach(() => {
  clearTokens();
  enableSessionRecovery(null);
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('session renewal lifecycle', () => {
  it('locks at expiry, retains the refresh token and does not count polling as activity', async () => {
    const post = vi.spyOn(axios, 'post');
    setTokens({ access_token: 'access', refresh_token: 'refresh', expires_in: 600 });
    await vi.advanceTimersByTimeAsync(600_000);
    expect(post).not.toHaveBeenCalled();
    expect(getSessionRecovery()).toBe('renew');
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBe('refresh');
  });

  it('requires credentials after an authoritative refresh rejection without discarding page scope', async () => {
    localStorage.setItem('t75_user', JSON.stringify({ id: 'owner' }));
    localStorage.setItem('integral.scope', 'retained');
    setTokens({ access_token: 'access', refresh_token: 'refresh', expires_in: 60 });
    vi.spyOn(axios, 'post').mockRejectedValue({ response: { status: 401 } });
    expect(await refreshAccessToken()).toBeNull();
    expect(getSessionRecovery()).toBe('signin');
    expect(getRefreshToken()).toBeNull();
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem('integral.scope')).toBe('retained');
  });

  it.each([429, 503])('retains renewable credentials after HTTP %s', async status => {
    setTokens({ access_token: 'access', refresh_token: 'refresh', expires_in: 60 });
    vi.spyOn(axios, 'post').mockRejectedValue({ response: { status } });
    expect(await refreshAccessToken()).toBeNull();
    pauseForSessionRenewal();
    expect(getSessionRecovery()).toBe('renew');
    expect(getRefreshToken()).toBe('refresh');
  });

  it('discards a refresh response arriving after explicit sign-out', async () => {
    let resolve!: (value: unknown) => void;
    vi.spyOn(axios, 'post').mockImplementation(() => new Promise(done => { resolve = done; }));
    setTokens({ access_token: 'access', refresh_token: 'refresh', expires_in: 60 });
    const pending = refreshAccessToken();
    clearTokens();
    resolve({ data: { access_token: 'late', refresh_token: 'late-refresh' } });
    expect(await pending).toBeNull();
    expect(localStorage.getItem('t75_token')).toBeNull();
  });
});
