import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, Trash2 } from 'lucide-react';
import { LINE_ICON_STROKE } from '../../components/ui';
import type { LayoutMode, RegionSpec } from './viewDesignerTypes';

type RegionCanvasProps = {
  regions: RegionSpec[];
  selectedKey: string | null;
  mode: LayoutMode | string;
  onSelect: (key: string) => void;
  onReorder: (from: number, to: number) => void;
  onRemove: (key: string) => void;
  onModeChange: (mode: LayoutMode) => void;
};

const MODES: LayoutMode[] = ['stack', 'tabs', 'accordion', 'grid', 'flex'];

function SortableRegionRow({
  region,
  selected,
  onSelect,
  onRemove,
}: {
  region: RegionSpec;
  selected: boolean;
  onSelect: () => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: region.key });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  const subtitle =
    region.kind === 'form'
      ? `form · ${(region.fields || []).length} fields`
      : `view · ${region.view}`;

  return (
    <li
      ref={setNodeRef}
      style={style}
      data-testid={`region-row-${region.key}`}
      className={`flex items-center gap-2 rounded-[var(--radius-input)] border px-2 py-2 text-sm ${
        selected
          ? 'border-[var(--link)] bg-[var(--panel-2)]'
          : 'border-[var(--panel-border)] bg-[var(--panel)]'
      }`}
    >
      <button
        type="button"
        className="shrink-0 touch-none text-[var(--text-muted)] hover:text-[var(--text)] cursor-grab"
        aria-label={`Drag ${region.title || region.key}`}
        {...attributes}
        {...listeners}
      >
        <GripVertical size={14} strokeWidth={LINE_ICON_STROKE} />
      </button>
      <button
        type="button"
        className="min-w-0 flex-1 text-left"
        onClick={onSelect}
      >
        <div className="font-medium text-[var(--text)] truncate">
          {region.title || region.key}
        </div>
        <div className="text-[11px] text-[var(--text-muted)] truncate">{subtitle}</div>
      </button>
      <button
        type="button"
        className="shrink-0 p-1 text-[var(--text-muted)] hover:text-[var(--danger)]"
        aria-label={`Remove ${region.key}`}
        onClick={onRemove}
      >
        <Trash2 size={13} strokeWidth={LINE_ICON_STROKE} />
      </button>
    </li>
  );
}

export function RegionCanvas({
  regions,
  selectedKey,
  mode,
  onSelect,
  onReorder,
  onRemove,
  onModeChange,
}: RegionCanvasProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const resolvedMode: LayoutMode =
    typeof mode === 'string' && MODES.includes(mode as LayoutMode)
      ? (mode as LayoutMode)
      : 'stack';

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = regions.findIndex(r => r.key === active.id);
    const to = regions.findIndex(r => r.key === over.id);
    if (from >= 0 && to >= 0) onReorder(from, to);
  };

  return (
    <div data-testid="region-canvas" className="flex flex-col gap-3 h-full min-h-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] uppercase tracking-wide text-[var(--text-subtle)]">
          Mode
        </span>
        <select
          className="text-sm rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1"
          value={resolvedMode}
          onChange={e => onModeChange(e.target.value as LayoutMode)}
          aria-label="Layout mode"
        >
          {MODES.map(m => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      {regions.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)] py-6 text-center border border-dashed border-[var(--panel-border)] rounded-[var(--radius-card)]">
          No regions yet. Add a form or nested view from the palette.
        </p>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={regions.map(r => r.key)}
            strategy={verticalListSortingStrategy}
          >
            <ul className="space-y-2 overflow-y-auto flex-1 min-h-0 pr-1">
              {regions.map(region => (
                <SortableRegionRow
                  key={region.key}
                  region={region}
                  selected={selectedKey === region.key}
                  onSelect={() => onSelect(region.key)}
                  onRemove={() => onRemove(region.key)}
                />
              ))}
            </ul>
          </SortableContext>
        </DndContext>
      )}
    </div>
  );
}
