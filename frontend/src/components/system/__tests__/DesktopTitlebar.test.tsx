/**
 * Desktop shell mimic title bar — the transparent drag strip under macOS
 * traffic lights, and the `--system-bar-h` accounting that clears it.
 *
 * Regression cover for "unable to drag the bar": in the collapsed-rail
 * state no in-app surface was grabbable, so the shell gets a dedicated
 * 40px drag strip (App's DesktopTitlebar) and the system-bar offset var
 * carries it everywhere the banner height already goes.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { act, render } from '@testing-library/react';
import { DesktopTitlebar } from '../DesktopTitlebar';
import { SystemNotificationBar } from '../SystemNotificationBar';
import {
  SystemNotificationsProvider,
  getSystemNotificationsApi,
} from '../SystemNotificationsContext';

const CSS_VAR = '--system-bar-h';

function stubDesktopBridge(platform: string | null): void {
  const w = window as unknown as Record<string, unknown>;
  if (platform === null) {
    delete w.integralDesktop;
  } else {
    w.integralDesktop = { platform, getApiUrl: () => null };
  }
}

afterEach(() => {
  stubDesktopBridge(null);
  document.documentElement.style.removeProperty(CSS_VAR);
  document.documentElement.style.removeProperty('--desktop-titlebar-h');
});

describe('DesktopTitlebar', () => {
  it('renders nothing in a plain browser', () => {
    stubDesktopBridge(null);
    const { container } = render(<DesktopTitlebar />);
    expect(container.firstChild).toBeNull();
  });

  it('renders a fixed drag strip in the macOS shell', () => {
    stubDesktopBridge('darwin');
    const { container } = render(<DesktopTitlebar />);
    const strip = container.firstChild as HTMLElement | null;
    expect(strip).not.toBeNull();
    expect(strip?.className).toContain('fixed');
    expect(strip?.className).toContain('[-webkit-app-region:drag]');
  });

  it('is opaque so scrolled content never shows through', () => {
    stubDesktopBridge('darwin');
    const { container } = render(<DesktopTitlebar />);
    const strip = container.firstChild as HTMLElement | null;
    expect(strip?.className).toContain('bg-[var(--bg)]');
  });

  it('publishes --desktop-titlebar-h on :root in the macOS shell', () => {
    stubDesktopBridge('darwin');
    render(<DesktopTitlebar />);
    expect(
      document.documentElement.style.getPropertyValue('--desktop-titlebar-h'),
    ).toBe('40px');
  });
});

describe('SystemNotificationBar shell accounting', () => {
  function renderBar() {
    return render(
      <SystemNotificationsProvider>
        <SystemNotificationBar />
      </SystemNotificationsProvider>,
    );
  }

  it('reserves 0px with no banner in a plain browser', () => {
    stubDesktopBridge(null);
    renderBar();
    expect(document.documentElement.style.getPropertyValue(CSS_VAR)).toBe(
      '0px',
    );
  });

  it('reserves the title bar height with no banner in the macOS shell', () => {
    stubDesktopBridge('darwin');
    renderBar();
    // The strip still occupies 40px even when no banner is showing.
    expect(document.documentElement.style.getPropertyValue(CSS_VAR)).toBe(
      '40px',
    );
  });

  it('offsets itself below the title bar', () => {
    stubDesktopBridge('darwin');
    const { container } = renderBar();
    act(() => {
      getSystemNotificationsApi()?.notify({ type: 'info', title: 'Hi' });
    });
    const bar = container.firstChild as HTMLElement | null;
    expect(bar).not.toBeNull();
    expect(bar?.className).toContain('top-[var(--desktop-titlebar-h,0px)]');
  });
});
