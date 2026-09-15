import { useState, useRef, useEffect } from 'react';
import { Bell, BellOff, Users } from 'lucide-react';
import { Avatar, LINE_ICON_STROKE } from '../ui';

interface WatcherUser {
  id: string;
  display_name?: string;
  email?: string;
  avatar_url?: string;
  avatar_attachment_id?: string;
  updated_at?: string;
}

interface WatchersControlProps {
  entryId?: string;
  watchers: WatcherUser[];
  isWatching: boolean;
  onToggle(): void;
  scope?: 'entry' | 'track';
}

export function WatchersControl({
  watchers,
  isWatching,
  onToggle,
  scope = 'entry',
}: WatchersControlProps) {
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

  return (
    <div className="relative inline-block" ref={wrapperRef}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`
          inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium border
          transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]
          ${isWatching
            ? 'bg-[var(--brand-accent)]/15 text-[var(--brand-accent)] border-[var(--brand-accent)]/30 hover:bg-[var(--brand-accent)]/25'
            : 'text-[var(--text-subtle)] border-transparent hover:text-[var(--text)] hover:bg-[var(--panel-2)]'
          }
        `}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Manage watchers"
      >
        {isWatching ? (
          <Bell size={14} strokeWidth={LINE_ICON_STROKE} />
        ) : (
          <BellOff size={14} strokeWidth={LINE_ICON_STROKE} />
        )}
        <span className="tabular-nums">{watchers.length}</span>
      </button>

      {open && (
        <div
          role="menu"
          className="
            absolute right-0 top-full mt-1 z-30 w-64 rounded-[var(--radius-card)]
            border border-[var(--panel-border)] bg-[var(--panel)] shadow-[var(--shadow-pop)]
            py-2 text-left animate-fade-in
          "
          onClick={e => e.stopPropagation()}
        >
          <div className="px-3 py-1.5 border-b border-[var(--panel-border)] flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-subtle)] flex items-center gap-1">
              <Users size={12} strokeWidth={LINE_ICON_STROKE} />
              Watchers ({watchers.length})
            </span>
          </div>

          <div className="max-h-48 overflow-y-auto py-1">
            {watchers.length === 0 ? (
              <p className="px-3 py-2 text-xs text-[var(--text-muted)] italic">
                No one is watching this {scope} yet.
              </p>
            ) : (
              watchers.map(w => {
                const name = w.display_name || w.email || 'Unknown User';
                return (
                  <div key={w.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-[var(--panel-2)] transition-colors">
                    <Avatar
                      name={name}
                      url={w.avatar_url}
                      attachmentId={w.avatar_attachment_id}
                      userId={w.id}
                      version={w.updated_at}
                      size="xs"
                      ringVariant="none"
                    />
                    <span className="truncate text-xs font-medium text-[var(--text)]" title={name}>
                      {name}
                    </span>
                  </div>
                );
              })
            )}
          </div>

          <div className="mt-1.5 px-2 pt-1.5 border-t border-[var(--panel-border)]">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onToggle();
              }}
              className={`
                w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors
                ${isWatching
                  ? 'bg-[var(--danger-bg)] text-[var(--danger-fg)] hover:bg-[var(--danger-bg)]/80'
                  : 'bg-[var(--brand-accent)] text-white hover:bg-[var(--brand-accent)]/90'
                }
              `}
            >
              {isWatching ? (
                <>
                  <BellOff size={12} strokeWidth={LINE_ICON_STROKE} />
                  Stop watching
                </>
              ) : (
                <>
                  <Bell size={12} strokeWidth={LINE_ICON_STROKE} />
                  Watch {scope}
                </>
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
