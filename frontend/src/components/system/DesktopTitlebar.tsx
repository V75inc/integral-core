import { useEffect } from 'react';
import { DESKTOP_TITLEBAR_PX, hasDesktopTitlebar } from '../../config';

/**
 * Desktop shell mimic title bar (macOS hidden-titlebar mode only, else null).
 *
 * A fixed strip at the very top of the viewport — the window's primary drag
 * surface ("natural padding" under the floating traffic lights). Solid
 * --bg (not transparent) so scrolled page content never shows through
 * behind the traffic lights — same opaque-chrome rule the system
 * notification bar follows. It publishes `--desktop-titlebar-h` on :root
 * so the system notification bar slides below it and every
 * `--system-bar-h` consumer (layout padding, sidebar, dock, chat heights)
 * clears both at once. Browsers never set the var, so all of this is a
 * no-op there.
 */
export function DesktopTitlebar() {
  useEffect(() => {
    if (!hasDesktopTitlebar()) return;
    document.documentElement.style.setProperty(
      '--desktop-titlebar-h',
      `${DESKTOP_TITLEBAR_PX}px`,
    );
    return () => {
      document.documentElement.style.removeProperty('--desktop-titlebar-h');
    };
  }, []);
  if (!hasDesktopTitlebar()) return null;
  return (
    <div
      aria-hidden
      className="fixed top-0 inset-x-0 z-system-bar h-10 bg-[var(--bg)] [-webkit-app-region:drag]"
    />
  );
}
