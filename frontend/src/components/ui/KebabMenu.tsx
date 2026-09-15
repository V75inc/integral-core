import { useEffect, useRef, useState, type ReactNode } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { Button } from './Button';
import { LINE_ICON_STROKE } from './IconWell';

export interface KebabMenuItem {
  /** Stable key. */
  key: string;
  /** Visible label. */
  label: string;
  /** Optional leading glyph (lucide icon). */
  icon?: ReactNode;
  /** Action to run on click. */
  onClick: () => void;
  /** Danger tone — uses danger fg/bg tokens. */
  danger?: boolean;
  /** Disable the item. */
  disabled?: boolean;
  /** Render a divider above this item. */
  dividerAbove?: boolean;
}

interface KebabMenuProps {
  items: KebabMenuItem[];
  /** Accessible label for the trigger button. */
  ariaLabel?: string;
  /** Match the size of sibling Button size="sm" so the trigger sits in
   *  the same baseline as inline action buttons. */
  size?: 'sm' | 'md';
}

/** Three-dot overflow menu for secondary / destructive actions.
 *
 *  Pattern lifted from `EntryCard.tsx` owner-actions menu (showMenu state +
 *  outside-click + Escape close), repackaged as a reusable primitive so
 *  detail pages don't reimplement the same plumbing per surface. */
export function KebabMenu({
  items,
  ariaLabel = 'More actions',
  size = 'sm',
}: KebabMenuProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t || !wrapperRef.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Use the shared Button primitive as the trigger so the kebab inherits
  // the exact same height/padding/border/font model as neighbouring
  // outline buttons in the action row. Override horizontal padding to
  // ``px-2`` so an icon-only trigger reads as roughly square instead of
  // pill-shaped (Button sm default is ``px-3``).
  return (
    <div className="relative" ref={wrapperRef}>
      <Button
        variant="outline"
        size={size}
        onClick={() => setOpen(s => !s)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={ariaLabel}
        /* ``!px-2`` keeps the icon-only trigger near-square (Button sm
           defaults to ``px-3`` for label width). Icon-only Buttons render
           no text run, so the line-box that normally sets ``text-sm``
           line-height (20px) is absent and the content collapses to the
           icon height (16px) — leaving the trigger ~4px shorter than
           sibling labelled buttons. ``min-h-[N]`` restores the height to
           match: sm = 34px (line-height 20 + py-1.5 12 + border 2), md =
           38px (line-height 20 + py-2 16 + border 2). */
        className={[
          '!p-0 text-[var(--text-muted)] hover:text-[var(--text)]',
          /* Square trigger sized to the labelled-button height:
               sm = 36×36 (matches Button sm outline 36px)
               md = 40×40 (matches Button md outline 40px)
             ``!p-0`` overrides Button's default ``px-3 py-1.5`` so the
             explicit width/height drive geometry and the icon centers via
             Button's intrinsic ``items-center justify-center``. */
          size === 'md' ? '!h-10 !w-10' : '!h-9 !w-9',
        ].join(' ')}
        icon={<MoreHorizontal size={16} strokeWidth={LINE_ICON_STROKE} />}
      />
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1 z-30 min-w-[200px] max-w-[calc(100vw-2rem)] rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel)] shadow-[var(--shadow-pop)] py-1 text-left animate-fade-in"
          onClick={e => e.stopPropagation()}
        >
          {items.map(item => (
            <div key={item.key}>
              {item.dividerAbove ? (
                <div className="my-1 h-px bg-[var(--panel-border)]" aria-hidden />
              ) : null}
              <button
                type="button"
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  if (item.disabled) return;
                  setOpen(false);
                  item.onClick();
                }}
                className={[
                  'flex items-center gap-2 w-full px-3 py-2 text-sm',
                  item.disabled
                    ? 'opacity-50 cursor-not-allowed text-[var(--text-muted)]'
                    : item.danger
                      ? 'text-[var(--danger-fg)] hover:bg-[var(--danger-bg)]'
                      : 'text-[var(--text)] hover:bg-[var(--panel-2)]',
                ].join(' ')}
              >
                {item.icon ? (
                  <span className="shrink-0 inline-flex items-center justify-center">
                    {item.icon}
                  </span>
                ) : null}
                <span className="truncate">{item.label}</span>
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
