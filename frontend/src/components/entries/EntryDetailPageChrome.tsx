import { ArrowLeft } from 'lucide-react';
import { IconWell, LINE_ICON_STROKE } from '../ui/IconWell';

/**
 * Full-page chrome for EntryDetail's `variant="page"` mode. Deliberately
 * accepts Modal's FULL prop shape (including props it ignores — `open`,
 * `disableEscape`, `width`, `variant`, `initialFocusRef`) so EntryDetail
 * can pick between `Modal` and this component with a single `const Chrome
 * = variant === 'page' ? EntryDetailPageChrome : Modal` swap, instead of
 * restructuring its ~350 lines of inner content (header fields, related
 * views, comments, activity) into a shared variable. Opt-in per entry type
 * via `form_schema.open_as_page` — every entry type that doesn't set it
 * keeps using Modal exactly as before, unaffected by this file's existence.
 */
export function EntryDetailPageChrome({
  title,
  titleIcon,
  headerActions,
  onClose,
  children,
}: {
  open?: boolean;
  onClose(): void;
  title?: string | React.ReactNode;
  titleIcon?: React.ReactNode;
  headerActions?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
  variant?: 'default' | 'compact';
  disableEscape?: boolean;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  return (
    <div className="min-h-screen bg-[var(--bg)]">
      <header
        className="
          sticky top-0 z-10 flex items-center justify-between gap-3
          border-b border-[var(--panel-border)] bg-[var(--panel)]
          px-4 sm:px-6 py-3
        "
      >
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onClose}
            aria-label="Back"
            className="
              flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-input)]
              text-[var(--text-muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]
              transition-colors duration-fast
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]
            "
          >
            <ArrowLeft size={16} strokeWidth={LINE_ICON_STROKE} />
          </button>
          {titleIcon ? (
            <IconWell size="sm">{titleIcon}</IconWell>
          ) : null}
          <h1 className="min-w-0 truncate text-[15px] font-semibold text-[var(--text)]">
            {title}
          </h1>
        </div>
        {headerActions ? (
          <div className="flex shrink-0 items-center gap-1">{headerActions}</div>
        ) : null}
      </header>
      <div className="mx-auto max-w-page px-4 sm:px-6 md:px-10 py-4 sm:py-5">
        {children}
      </div>
    </div>
  );
}
