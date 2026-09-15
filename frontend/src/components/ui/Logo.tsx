/**
 * Logo — Integral brand mark + wordmark.
 *
 * Single source of truth for the brand presentation. Used in:
 *   - Sidebar (top-left in app chrome)
 *   - Auth pages (LoginPage / SignupPage / Forgot / Reset)
 *
 * The mark is a small rounded-square with a subtle gradient fill and an
 * inner cutout that takes the page background, exactly matching the
 * Direction A · Quiet Premium spec. The wordmark sits beside it at
 * 17px / weight 600 / tracking -0.02em.
 *
 * Pass `size="sm"` for compact placements (sidebar) and `size="lg"` for
 * hero placements (auth pages). The mark and wordmark scale together.
 */

import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';
import { PRODUCT_NAME } from '../../brand';

type LogoSize = 'xs' | 'sm' | 'md' | 'lg';

interface LogoProps {
  /** Optional href; renders a Link when set, otherwise a plain span. */
  to?: string;
  size?: LogoSize;
  /** Render only the mark, no wordmark. */
  markOnly?: boolean;
  className?: string;
  /** When the wordmark is rendered as a custom element (e.g. an H1 on
   *  the auth page hero), pass it here to replace the default span. */
  wordmark?: ReactNode;
  ariaLabel?: string;
}

const SIZE_TOKENS: Record<
  LogoSize,
  { mark: number; cutout: number; radius: number; word: string; gap: string }
> = {
  xs: { mark: 14, cutout: 3, radius: 4, word: 'text-[12px]', gap: 'gap-1.5' },
  sm: { mark: 22, cutout: 5, radius: 6, word: 'text-[15px]', gap: 'gap-2' },
  md: { mark: 28, cutout: 6, radius: 7, word: 'text-[17px]', gap: 'gap-2.5' },
  lg: { mark: 40, cutout: 9, radius: 10, word: 'text-[22px]', gap: 'gap-3' },
};

export function LogoMark({ size = 'sm' }: { size?: LogoSize }) {
  const t = SIZE_TOKENS[size];
  return (
    <span
      aria-hidden
      className="relative inline-block shrink-0"
      style={{
        width: t.mark,
        height: t.mark,
        borderRadius: t.radius,
        // Theme-aware gradient — dark on light canvas, light on dark
        // canvas. Tokens defined in index.css per-theme.
        background:
          'linear-gradient(135deg, var(--logo-mark-from) 0%, var(--logo-mark-to) 100%)',
      }}
    >
      <span
        className="absolute"
        style={{
          inset: t.cutout,
          borderRadius: Math.max(2, t.radius - 4),
          background: 'var(--bg)',
        }}
      />
    </span>
  );
}

export function Logo({
  to,
  size = 'sm',
  markOnly = false,
  className = '',
  wordmark,
  ariaLabel,
}: LogoProps) {
  const t = SIZE_TOKENS[size];
  const inner = (
    <>
      <LogoMark size={size} />
      {!markOnly &&
        (wordmark ?? (
          <span
            className={`${t.word} font-semibold tracking-[-0.02em] text-[var(--text)] truncate`}
          >
            {PRODUCT_NAME}
          </span>
        ))}
    </>
  );
  const baseClasses = `inline-flex items-center ${t.gap} min-w-0 ${className}`;
  if (to) {
    return (
      <Link
        to={to}
        aria-label={ariaLabel ?? `${PRODUCT_NAME} — go to dashboard`}
        className={`${baseClasses} group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)] rounded-md`}
      >
        {inner}
      </Link>
    );
  }
  return <span className={baseClasses}>{inner}</span>;
}
