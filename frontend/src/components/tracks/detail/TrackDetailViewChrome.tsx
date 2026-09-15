import {
  PanelRightClose,
  PanelRightOpen,
  Settings,
} from 'lucide-react';
import {
  LINE_ICON_STROKE,
  PageSection,
  ViewTabs,
  type ViewTabOption,
} from '../../ui';
import type { SavedView } from '../../../types';

function railToggleClass(active: boolean): string {
  return `
    hidden xl:inline-flex items-center justify-center
    w-8 h-8 rounded-[var(--radius-input)]
    transition-colors duration-fast
    ${
      active
        ? 'text-[color:var(--text)] bg-[color:var(--panel-2)]'
        : 'text-[color:var(--text-subtle)] hover:text-[color:var(--text)] hover:bg-[color:var(--panel-2)]'
    }
  `;
}

const idleToggleClass = `
  hidden xl:inline-flex items-center justify-center
  w-8 h-8 rounded-[var(--radius-input)]
  text-[color:var(--text-subtle)]
  hover:text-[color:var(--text)] hover:bg-[color:var(--panel-2)]
  transition-colors duration-fast
`;

export interface TrackDetailViewChromeProps {
  trackId: string | undefined;
  viewTabOptions: ViewTabOption[];
  activeView: SavedView | null;
  dedupedTabViews: SavedView[];
  onChangeView: (view: SavedView) => void;
  canViewTrackConfig: boolean;
  trackConfigOpen: boolean;
  trackActivityOpen: boolean;
  onToggleConfig: () => void;
  onToggleActivity: () => void;
}

/** View tabs + right-rail toggles (or plain divider when no tabs). */
export function TrackDetailViewChrome({
  trackId,
  viewTabOptions,
  activeView,
  dedupedTabViews,
  onChangeView,
  canViewTrackConfig,
  trackConfigOpen,
  trackActivityOpen,
  onToggleConfig,
  onToggleActivity,
}: TrackDetailViewChromeProps) {
  if (!trackId) {
    return <PageSection.Separator />;
  }

  const configToggle = canViewTrackConfig ? (
    <button
      type="button"
      onClick={onToggleConfig}
      aria-expanded={trackConfigOpen}
      aria-label={
        trackConfigOpen
          ? 'Hide track configuration'
          : 'Show track configuration'
      }
      className={railToggleClass(trackConfigOpen)}
    >
      <Settings size={16} strokeWidth={LINE_ICON_STROKE} aria-hidden />
    </button>
  ) : null;

  const activityToggle = (
    <button
      type="button"
      onClick={onToggleActivity}
      aria-expanded={trackActivityOpen}
      aria-label={
        trackActivityOpen ? 'Hide activity rail' : 'Show activity rail'
      }
      className={idleToggleClass}
    >
      {trackActivityOpen ? (
        <PanelRightClose
          size={16}
          strokeWidth={LINE_ICON_STROKE}
          aria-hidden
        />
      ) : (
        <PanelRightOpen
          size={16}
          strokeWidth={LINE_ICON_STROKE}
          aria-hidden
        />
      )}
    </button>
  );

  if (viewTabOptions.length >= 1 && activeView) {
    return (
      <div className="border-b border-[var(--panel-border)]">
        <PageSection>
          <ViewTabs
            options={viewTabOptions}
            value={activeView.id}
            onChange={nextId => {
              const next = dedupedTabViews.find(v => v.id === nextId);
              if (next) onChangeView(next);
            }}
            ariaLabel="Track views"
            noBorder
            actions={
              <div className="flex items-center gap-1">
                {configToggle}
                {activityToggle}
              </div>
            }
          />
        </PageSection>
      </div>
    );
  }

  return (
    <div className="border-b border-[var(--panel-border)]">
      <PageSection innerClassName="flex items-center justify-end gap-1 py-1.5">
        {configToggle}
        {activityToggle}
      </PageSection>
    </div>
  );
}
