import { ArrowLeft } from 'lucide-react';
import { IconWell, LINE_ICON_STROKE } from '../ui/IconWell';
import { IconButton, Surface, Text } from '../../ui';

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
  canvas,
}: {
  open?: boolean;
  onClose(): void;
  title?: string | React.ReactNode;
  titleIcon?: React.ReactNode;
  headerActions?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
  tall?: boolean;
  sidePanel?: React.ReactNode;
  hasCompanionPanel?: boolean;
  allowAssistantDock?: boolean;
  variant?: 'default' | 'compact';
  disableEscape?: boolean;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  /** Opt-in (entry type ``canvas``): the entry's file, shown beside its fields. */
  canvas?: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-[var(--bg)]">
      <Surface
        as="header"
        tone="panel"
        border="none"
        radius="none"
        className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-[var(--panel-border)] px-4 py-3 sm:px-6"
      >
        <div className="flex min-w-0 items-center gap-3">
          <IconButton
            label="Back"
            onClick={onClose}
            size="md"
            tone="muted"
            className="shrink-0"
          >
            <ArrowLeft size={16} strokeWidth={LINE_ICON_STROKE} />
          </IconButton>
          {titleIcon ? (
            <IconWell size="sm">{titleIcon}</IconWell>
          ) : null}
          <Text as="h1" variant="heading-sm" weight="semibold" className="flex min-w-0 items-center gap-2.5">
            {title}
          </Text>
        </div>
        {headerActions ? (
          <div className="flex shrink-0 items-center gap-1">{headerActions}</div>
        ) : null}
      </Surface>
      {canvas ? (
        <div
          data-testid="entry-canvas-layout"
          className="mx-auto grid max-w-[1800px] gap-4 px-4 py-4 sm:px-6 lg:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]"
        >
          <div className="min-w-0 lg:sticky lg:top-[64px] lg:h-[calc(100vh-5.5rem)] lg:self-start">
            {canvas}
          </div>
          <div className="min-w-0">{children}</div>
        </div>
      ) : (
        <div className="mx-auto max-w-page px-4 sm:px-6 md:px-10 py-4 sm:py-5">
          {children}
        </div>
      )}
    </div>
  );
}
