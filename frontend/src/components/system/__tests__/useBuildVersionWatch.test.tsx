/**
 * Stale-build watcher stays out of the desktop shell's way.
 *
 * The shell loads its renderer from file:// — a reload can never fetch a
 * newer bundle, so probing /api/meta/build there would reload once and
 * then nag forever. Shell updates are a packaging concern.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render } from '@testing-library/react';
import { SystemNotificationsProvider } from '../SystemNotificationsContext';
import { useBuildVersionWatch } from '../useBuildVersionWatch';

function stubDesktopBridge(platform: string | null): void {
  const w = window as unknown as Record<string, unknown>;
  if (platform === null) {
    delete w.integralDesktop;
  } else {
    w.integralDesktop = { platform, getApiUrl: () => null };
  }
}

function Probe() {
  useBuildVersionWatch();
  return null;
}

function renderProbe() {
  return render(
    <SystemNotificationsProvider>
      <Probe />
    </SystemNotificationsProvider>,
  );
}

afterEach(() => {
  stubDesktopBridge(null);
  vi.unstubAllGlobals();
});

describe('useBuildVersionWatch', () => {
  it('never probes the build endpoint inside the desktop shell', () => {
    stubDesktopBridge('darwin');
    const fetchMock = vi.fn().mockResolvedValue({ ok: false });
    vi.stubGlobal('fetch', fetchMock);
    renderProbe();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('still probes in a plain browser', () => {
    stubDesktopBridge(null);
    const fetchMock = vi.fn().mockResolvedValue({ ok: false });
    vi.stubGlobal('fetch', fetchMock);
    renderProbe();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/meta/build');
  });
});
