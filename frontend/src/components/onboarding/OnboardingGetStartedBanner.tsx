/**
 * Phase 9 Plan 09-05 (ONBD-01) — A5 fallback banner.
 *
 * Brief first-login guidance with a link to the reusable help content.
 */
import { Link } from 'react-router-dom';

import { useFirstLoginOnboarding } from '../../hooks/useFirstLoginOnboarding';

export function OnboardingGetStartedBanner() {
  const { dismiss } = useFirstLoginOnboarding();
  return (
    <div
      role="status"
      aria-live="polite"
      className="
        sticky top-0 z-topbar flex items-center justify-between gap-3
        border-b border-[var(--border-subtle)]
        bg-[var(--brand-accent-soft)]
        px-4 py-2 text-sm text-[var(--text)]
      "
    >
      <span>
        Welcome to Integral. Apps bring related work together, Tracks organize it, and Entries hold individual records. Visit{' '}
        <Link
          to="/settings#get-started"
          className="text-[var(--brand-accent)] underline"
        >
          Help and getting started
        </Link>{' '}
        to begin.
      </span>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss welcome banner"
        className="
          flex h-6 w-6 items-center justify-center rounded-[var(--radius-input)]
          text-[var(--text-muted)]
          hover:bg-[var(--panel-2)] hover:text-[var(--text)] transition
          focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)]
        "
      >
        ×
      </button>
    </div>
  );
}
