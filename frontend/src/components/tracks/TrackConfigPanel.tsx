import { useState, useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Eye,
  EyeOff,
  Package,
  Pencil,
  Plus,
  Star,
  Trash2
} from 'lucide-react';
import {
  tagsApi,
  entryTypesApi,
  trackViewsApi,
  contentProfilesApi
} from '../../api';
import { AppSelect, Button, KebabMenu, LINE_ICON_STROKE } from '../ui';
import { listWidgets } from '../views';
import { useConfirm } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import {
  entryTypesForTrackQueryKey,
  tagsForTrackQueryKey,
  viewsForTrackQueryKey
} from '../../queryKeys';
import { SchemaSection } from './settings/SchemaSection';
import { ViewSettingsModal } from './ViewSettingsModal';
import type { SavedView } from '../../types';

interface TrackConfigPanelProps {
  trackId: string;
  canEdit: boolean;
}

export function TrackConfigPanel({ trackId, canEdit }: TrackConfigPanelProps) {
  const confirm = useConfirm();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const entryTypesQuery = useQuery({
    queryKey: entryTypesForTrackQueryKey(trackId),
    queryFn: () => entryTypesApi.list({ track_id: trackId })
  });
  const entryTypes = useMemo(
    () => entryTypesQuery.data ?? [],
    [entryTypesQuery.data],
  );

  const tagsQuery = useQuery({
    queryKey: tagsForTrackQueryKey(trackId),
    queryFn: () => tagsApi.list({ track_id: trackId })
  });
  const tags = tagsQuery.data ?? [];

  const viewsQuery = useQuery({
    queryKey: viewsForTrackQueryKey(trackId),
    queryFn: () => trackViewsApi.list(trackId)
  });
  const views = useMemo(() => viewsQuery.data ?? [], [viewsQuery.data]);

  const loading =
    entryTypesQuery.isLoading || tagsQuery.isLoading || viewsQuery.isLoading;
  const loadError = tagsQuery.isError || viewsQuery.isError;

  useEffect(() => {
    if (loadError) {
      showToast('Failed to load track configuration', 'error');
    }
  }, [loadError, showToast]);

  // New item form state
  const [newTag, setNewTag] = useState('');
  const [newTagColor, setNewTagColor] = useState('#6B7280');
  const [newType, setNewType] = useState('');
  const [newViewName, setNewViewName] = useState('');
  const [newViewType, setNewViewType] = useState('kanban');
  /** Calendar-specific binding for the new view; only used when
   *  ``newViewType === 'calendar'``. Default to ``created_at`` so a freshly
   *  added Calendar view shows entries by their creation timestamp out of
   *  the box. */
  const [newViewDateField, setNewViewDateField] = useState('created_at');

  // Full registered widget catalog (backend `app/views/contracts/*.json`
  // sync'd into frontend `views/manifests/*` at boot via plugins/auto.ts).
  // Exclude types already present on this track — one view per type is
  // the substrate rule, so showing duplicates would just produce a
  // guaranteed "A <type> view already exists" toast on submit.
  const availableWidgetOptions = useMemo(() => {
    const taken = new Set(
      views.map(v => String(v.type || '').trim().toLowerCase()).filter(Boolean)
    );
    return listWidgets()
      .filter(reg => !taken.has(String(reg.type || '').trim().toLowerCase()))
      .map(reg => ({ value: reg.type, label: reg.meta.label }));
  }, [views]);

  /** Date / datetime fields from the track's EntryTypes plus the two
   *  Entry-level audit timestamps. Used to populate the calendar
   *  date-field picker so the Calendar widget knows which field to anchor
   *  entries on. */
  const dateFieldOptions = useMemo(() => {
    const out: { value: string; label: string }[] = [
      { value: 'created_at', label: 'Created at (default)' },
      { value: 'updated_at', label: 'Updated at' },
    ];
    const seen = new Set(out.map(o => o.value));
    for (const et of entryTypes) {
      const fields = et.form_schema?.fields || [];
      for (const f of fields) {
        if (!f?.key) continue;
        if (f.type !== 'date' && f.type !== 'datetime') continue;
        if (seen.has(f.key)) continue;
        seen.add(f.key);
        out.push({
          value: f.key,
          label: `${f.name || f.key} (${et.name})`
        });
      }
    }
    return out;
  }, [entryTypes]);

  useEffect(() => {
    if (!availableWidgetOptions.length) {
      // All registered types already on the track — clear selection so
      // the picker shows a placeholder and the submit guard fires.
      if (newViewType) setNewViewType('');
      return;
    }
    if (!availableWidgetOptions.some(opt => opt.value === newViewType)) {
      setNewViewType(availableWidgetOptions[0].value);
    }
  }, [availableWidgetOptions, newViewType]);

  // --- Actions ---

  const addTag = async () => {
    if (!newTag.trim()) return;
    try {
      await tagsApi.create({
        track_id: trackId,
        name: newTag.trim(),
        color: newTagColor
      });
      setNewTag('');
      await queryClient.invalidateQueries({
        queryKey: tagsForTrackQueryKey(trackId)
      });
      showToast('Tag created', 'success');
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed',
        'error'
      );
    }
  };

  const delTag = async (id: string) => {
    const ok = await confirm({
      title: 'Delete tag',
      message: 'Delete this tag? Entries that use it may lose the tag reference.',
      confirmLabel: 'Delete',
      variant: 'danger'
    });
    if (!ok) return;
    try {
      await tagsApi.delete(id);
      await queryClient.invalidateQueries({
        queryKey: tagsForTrackQueryKey(trackId)
      });
    } catch {
      showToast('Failed to delete tag', 'error');
    }
  };

  const addType = async () => {
    if (!newType.trim()) return;
    try {
      await contentProfilesApi.addEntryTypeToTrackProfile(trackId, {
        name: newType.trim().toLowerCase()
      });
      setNewType('');
      await queryClient.invalidateQueries({
        queryKey: entryTypesForTrackQueryKey(trackId)
      });
      showToast('Entry type added to profile', 'success');
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed',
        'error'
      );
    }
  };

  const addView = async () => {
    if (!newViewName.trim()) return;
    if (views.some(v => v.type === newViewType)) {
      showToast(`A ${newViewType} view already exists`, 'error');
      return;
    }
    // Calendar views NEED a date_field binding to know where to place
    // entries on the grid; default to ``created_at`` if the user hasn't
    // picked a content-profile date field.
    const config: Record<string, unknown> =
      newViewType === 'calendar'
        ? {
            calendar_mapping: {
              date_field: newViewDateField || 'created_at'
            }
          }
        : newViewType === 'kanban'
          ? { group_by: 'custom_fields._kanban_stage' }
          : {};
    try {
      await contentProfilesApi.addViewToTrackProfile(trackId, {
        name: newViewName.trim(),
        view_type: newViewType,
        config,
        is_default: views.length === 0
      });
      setNewViewName('');
      setNewViewDateField('created_at');
      await queryClient.invalidateQueries({
        queryKey: viewsForTrackQueryKey(trackId)
      });
      showToast('View added to profile', 'success');
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed',
        'error'
      );
    }
  };

  const removeView = async (id: string) => {
    const ok = await confirm({
      title: 'Remove view',
      message: 'Remove this saved view? This cannot be undone.',
      confirmLabel: 'Remove',
      variant: 'danger'
    });
    if (!ok) return;
    try {
      await contentProfilesApi.removeViewFromTrackProfile(trackId, id);
      await queryClient.invalidateQueries({
        queryKey: viewsForTrackQueryKey(trackId)
      });
      showToast('View removed', 'success');
    } catch {
      showToast('Failed to remove view', 'error');
    }
  };

  // Currently-edited view (drives ViewSettingsModal). `null` = closed.
  const [editingView, setEditingView] = useState<SavedView | null>(null);

  const setViewHidden = async (id: string, hidden: boolean) => {
    try {
      await trackViewsApi.update(id, { hidden });
      await queryClient.refetchQueries({
        queryKey: viewsForTrackQueryKey(trackId),
        exact: true
      });
      showToast(hidden ? 'View hidden from tabs' : 'View shown in tabs', 'success');
    } catch (e) {
      console.error('setViewHidden error', e);
      showToast(
        hidden ? 'Failed to hide view' : 'Failed to show view',
        'error'
      );
    }
  };

  const setDefaultView = async (id: string) => {
    try {
      await trackViewsApi.update(id, { is_default: true });
      // Force immediate refetch — invalidateQueries alone may not
      // trigger a refetch when the observer is already active in v5.
      await queryClient.refetchQueries({
        queryKey: viewsForTrackQueryKey(trackId),
        exact: true
      });
      showToast('Default view updated', 'success');
    } catch (e) {
      console.error('setDefaultView error', e);
      showToast('Failed to update default view', 'error');
    }
  };

  const handleDeriveToLibrary = async () => {
    const ok = await confirm({
      title: 'Publish to library',
      message: 'Create a new library profile from this track\'s current configuration? This will be published as a platform profile.',
      confirmLabel: 'Publish',
      variant: 'default'
    });
    if (!ok) return;
    try {
      const result = await contentProfilesApi.deriveFromTrack(trackId);
      showToast(`Profile "${result.content_profile?.name || 'Untitled'}" published to library`, 'success');
    } catch {
      showToast('Failed to publish profile', 'error');
    }
  };

  // --- Render ---

  if (loading) {
    return (
      <div className="app-card p-4 text-sm text-[var(--text-muted)] animate-pulse">
        Loading configuration…
      </div>
    );
  }

  return (
    <div className="space-y-5">

      {/* ── Entry Types ─────────────────────────────── */}
      <section aria-label="Entry types">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--text-subtle)] mb-2 px-0.5">
          Entry Types
        </p>
        {canEdit && (
          <div className="flex items-stretch gap-2 mb-3">
            <input
              className="app-input text-xs flex-1"
              placeholder="New entry type slug (e.g. bug)"
              value={newType}
              onChange={e => setNewType(e.target.value)}
            />
            <Button size="sm" variant="outline" onClick={addType} className="self-stretch">
              <Plus size={12} /> Add type
            </Button>
          </div>
        )}
        <SchemaSection trackId={trackId} canEdit={canEdit} />
      </section>

      {/* ── Tags ───────────────────────────────────── */}
      <section aria-label="Tags">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--text-subtle)] mb-2 px-0.5">
          Tags
        </p>
      <div className="app-card p-4">
        <div className="flex flex-wrap gap-1.5 mb-3">
          {tags.map(tg => (
            <span
              key={tg.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs border border-[var(--panel-border)]"
              style={{ borderColor: tg.color || undefined }}
            >
              {tg.name}
              {canEdit && (
                <button
                  type="button"
                  className="text-[var(--danger-fg)] hover:underline"
                  onClick={() => delTag(tg.id)}
                  aria-label={`Delete tag ${tg.name}`}
                >
                  ×
                </button>
              )}
            </span>
          ))}
          {tags.length === 0 && (
            <span className="text-xs text-[var(--text-muted)]">No tags yet</span>
          )}
        </div>
        {canEdit && (
          <div className="flex items-stretch gap-2">
            <input
              className="app-input text-xs flex-1 min-w-[120px]"
              placeholder="New tag"
              value={newTag}
              onChange={e => setNewTag(e.target.value)}
            />
            <input
              type="color"
              className="w-10 rounded border border-[var(--panel-border)] cursor-pointer"
              style={{ height: '2.5625rem' }}
              value={newTagColor}
              onChange={e => setNewTagColor(e.target.value)}
              aria-label="Tag color"
            />
            <Button size="sm" variant="outline" onClick={addTag} className="self-stretch">
              <Plus size={12} /> Add
            </Button>
          </div>
        )}
      </div>
      </section>

      {/* ── Views ──────────────────────────────────── */}
      <section aria-label="Views">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--text-subtle)] mb-2 px-0.5">
          Views
        </p>
      <div className="app-card p-4">
        <ul className="space-y-2 mb-3">
          {views.map(v => {
            // Inline default badge stays as a visual signal; every
            // action (Edit / Set default / Hide / Remove) is folded
            // into the per-row kebab so the row stays scannable even
            // as the set of view operations grows.
            const isProtected = v.is_default || v.type === 'feed';
            const menuItems = canEdit
              ? [
                  {
                    key: 'edit',
                    label: 'Edit configuration',
                    icon: (
                      <Pencil size={13} strokeWidth={LINE_ICON_STROKE} />
                    ),
                    onClick: () => setEditingView(v)
                  },
                  ...(v.is_default
                    ? []
                    : [
                        {
                          key: 'set-default',
                          label: 'Set as default',
                          icon: (
                            <Star size={13} strokeWidth={LINE_ICON_STROKE} />
                          ),
                          onClick: () => setDefaultView(v.id)
                        },
                      ]),
                  {
                    key: 'visibility',
                    label: v.hidden ? 'Show in tab strip' : 'Hide from tab strip',
                    icon: v.hidden ? (
                      <Eye size={13} strokeWidth={LINE_ICON_STROKE} />
                    ) : (
                      <EyeOff size={13} strokeWidth={LINE_ICON_STROKE} />
                    ),
                    onClick: () => setViewHidden(v.id, !v.hidden)
                  },
                  ...(isProtected
                    ? []
                    : [
                        {
                          key: 'remove',
                          label: 'Remove view',
                          icon: (
                            <Trash2 size={13} strokeWidth={LINE_ICON_STROKE} />
                          ),
                          onClick: () => removeView(v.id),
                          danger: true,
                          dividerAbove: true
                        },
                      ]),
                ]
              : [];

            return (
              <li
                key={v.id}
                className={`flex items-center justify-between text-sm gap-2 py-0.5 ${
                  v.hidden ? 'opacity-55' : ''
                }`}
              >
                <span className="min-w-0 truncate">
                  {v.name}{' '}
                  <span className="text-[var(--text-muted)]">({v.type})</span>
                  {v.hidden && (
                    <span className="ml-1.5 text-[10px] uppercase tracking-wider text-[var(--text-subtle)]">
                      hidden
                    </span>
                  )}
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  {v.is_default && (
                    <span className="inline-flex items-center gap-1 text-xs text-[var(--link)]">
                      <Star
                        size={12}
                        strokeWidth={LINE_ICON_STROKE}
                        className="fill-current"
                      />
                      default
                    </span>
                  )}
                  {canEdit && menuItems.length > 0 && (
                    <KebabMenu
                      items={menuItems}
                      ariaLabel={`Actions for view ${v.name}`}
                    />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        {canEdit && (
          <div className="space-y-2">
            <input
              className="app-input text-xs w-full"
              placeholder="View name"
              value={newViewName}
              onChange={e => setNewViewName(e.target.value)}
            />
            <div className="flex items-stretch gap-2">
              <div className="flex-1 min-w-0 self-stretch">
                <AppSelect
                  className="app-input text-sm w-full h-full"
                  value={newViewType}
                  onValueChange={setNewViewType}
                  options={
                    availableWidgetOptions.length
                      ? availableWidgetOptions
                      : [{ value: '', label: 'All view types added' }]
                  }
                  disabled={!availableWidgetOptions.length}
                />
              </div>
              <Button
                size="sm"
                variant="primary"
                onClick={addView}
                disabled={!availableWidgetOptions.length || !newViewType}
                className="self-stretch"
              >
                <Plus size={12} /> Add view
              </Button>
            </div>
            {newViewType === 'calendar' && (
              <div className="pl-1">
                <label className="block text-[11px] font-medium text-[var(--text-muted)] mb-1">
                  Date field
                </label>
                <AppSelect
                  className="app-input text-xs w-full"
                  value={newViewDateField}
                  onValueChange={setNewViewDateField}
                  options={dateFieldOptions}
                />
                <p className="mt-1 text-[11px] text-[var(--text-subtle)]">
                  Entries with a value in this field will appear on the
                  calendar on that day.
                </p>
              </div>
            )}
          </div>
        )}
      </div>
      </section>

      {/* ── Configure ──────────────────────────────── */}
      <section aria-label="Configure">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--text-subtle)] mb-2 px-0.5">
          Configure
        </p>
        <div className="space-y-3">
          {/* Publish to library */}
          {canEdit && (
            <div className="app-card p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)] flex items-center gap-2 mb-2">
                <Package size={12} strokeWidth={LINE_ICON_STROKE} />
                Share to library
              </h3>
              <p className="text-xs text-[var(--text-muted)] mb-3">
                Create a reusable library profile from this track's current configuration.
              </p>
              <Button size="sm" variant="outline" onClick={handleDeriveToLibrary}>
                Publish to library
              </Button>
            </div>
          )}
        </div>
      </section>

      <ViewSettingsModal
        open={Boolean(editingView)}
        onClose={() => setEditingView(null)}
        view={editingView}
        trackId={trackId}
        entryTypes={entryTypes}
      />

    </div>
  );
}