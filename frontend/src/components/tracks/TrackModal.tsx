import { useState, useEffect, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Modal } from '../ui/Modal';
import { AppSelect, Button, VisibilityField, LINE_ICON_STROKE } from '../ui';
import { Plus, Upload } from 'lucide-react';
import type { VisibilityChoice } from '../ui';
import { Input, Textarea, Surface, Text } from '../../ui';
import { ImportPackageModal } from '../library/ImportPackageModal';
import { filterWorkspaceVisibleLibraryPackages } from '../apps/appBundleMatching';
import { getManifestScopeKind } from '../../lib/operationalModelManifest';
import { FormDialog, StepDialog } from '../../templates';
import {
  tracksApi,
  appsApi,
  operationalModelsApi,
} from '../../api';
import { invalidateWorkspaceListCaches } from '../../queryKeys';
import type { CreateTrackBody } from '../../api/tracks';
import { useConfirm } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import { useScope } from '../../context/ScopeContext';
import type { OperationalModelNode, App, Track } from '../../types';
import {
  manifestAppTracks,
} from '../../utils';
import { OperationalModelPicker } from '../library/OperationalModelPicker';
import type { OperationalModelPickerItem } from '../library/OperationalModelPicker';
import { summarizeLibraryManifest } from '../../lib/operationalModelManifest';
import { trackPath } from '../../utils/resourcePaths';

function normalizeTrackVisibility(v: string | undefined): VisibilityChoice {
  if (!v) return 'private';
  if (v === 'team' || v === 'organization') return 'workspace';
  if (v === 'inherit') return 'inherit';
  if (v === 'private' || v === 'workspace' || v === 'public') return v;
  return 'private';
}

interface TrackModalProps {
  open: boolean;
  onClose(): void;
  onCreated(track: Track): void | Promise<void>;
  editTrack?: Track | null;
  /** When creating from an App, enables operational model choices on create. */
  appId?: string;
}

export function TrackModal({
  open,
  onClose,
  onCreated,
  editTrack,
  appId,
}: TrackModalProps) {
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [deletingModels, setDeletingModels] = useState<Set<string>>(new Set());
  const { scope, activeWorkspace } = useScope();
  const isEdit = !!editTrack;
  const [title, setTitle] = useState(editTrack?.title || '');
  const [purpose, setPurpose] = useState(
    editTrack?.purpose || editTrack?.description || ''
  );
  const [visibilityChoice, setVisibilityChoice] = useState<VisibilityChoice>(() =>
    isEdit ? normalizeTrackVisibility(editTrack?.visibility) : 'private'
  );
  const [loading, setLoading] = useState(false);
  const queryClient = useQueryClient();
  const [appDetail, setAppDetail] = useState<App | null>(null);
  const [appCp, setAppCp] = useState<OperationalModelNode | null>(null);
  const [templates, setTemplates] = useState<OperationalModelNode[]>([]);
  const [libraryPackages, setLibraryPackages] = useState<OperationalModelNode[]>([]);
  const [operationalModelChoice, setOperationalModelChoice] = useState('');
  const [chosenParentAppId, setChosenParentAppId] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [parentAppOptions, setParentAppOptions] = useState<App[]>([]);

  type TrackModalStep = 'details' | 'profile';
  const [step, setStep] = useState<TrackModalStep>('profile');

  const manifestTracks = useMemo(
    () => manifestAppTracks(appCp?.manifest),
    [appCp?.manifest]
  );

  const showLibraryRow = useMemo(() => {
    if (!appId) return true;
    if (appDetail?.library_merge_source_id) return false;
    if (manifestTracks.length > 0) return false;
    return true;
  }, [appId, appDetail?.library_merge_source_id, manifestTracks.length]);

  const appSources = useMemo((): OperationalModelPickerItem[] => {
    const items: OperationalModelPickerItem[] = [];
    for (const mt of manifestTracks) {
      items.push({
        value: `manifest:${mt.key}`,
        name: mt.name,
        description: `Track type "${mt.key}" from this App's Operational Model.`,
        entryTypeCount: 0,
        viewCount: 0,
        source: 'app',
      });
    }
    for (const tpl of templates) {
      const manifest = (tpl.manifest ?? {}) as Record<string, unknown>;
      const { entryTypeCount, viewCount } = summarizeLibraryManifest(manifest);
      items.push({
        value: `template:${tpl.id}`,
        name: tpl.name || 'Untitled model',
        description: tpl.description?.trim().slice(0, 140) || 'App-defined track template.',
        entryTypeCount,
        viewCount,
        source: 'app',
      });
    }
    return items;
  }, [manifestTracks, templates]);

  useEffect(() => {
    if (open) {
      setStep('profile');
      setImportOpen(false);
      if (!isEdit) { setTitle(''); setPurpose(''); }
    }
  }, [open, isEdit, scope?.workspaceId]);

  useEffect(() => {
    if (!open || isEdit) {
      setAppDetail(null);
      setAppCp(null);
      setTemplates([]);
      setLibraryPackages([]);
      setOperationalModelChoice('');
      return;
    }
    let cancelled = false;
    setLibraryLoading(true);
    setLibraryError(null);
    const pApp = appId
      ? appsApi.get(appId).catch(() => null)
      : Promise.resolve(null);
    const pCp = appId
      ? appsApi.getOperationalModel(appId).catch(() => null)
      : Promise.resolve(null);
    const pTpl = appId
      ? appsApi.listTrackTemplates(appId).catch(() => [])
      : Promise.resolve([]);
    const pLib = operationalModelsApi.list().catch(() => {
      if (!cancelled) setLibraryError('Could not load operational models. You can retry or create a blank track.');
      return [];
    });
    Promise.all([pApp, pCp, pTpl, pLib]).then(([sp, cp, tpls, packs]) => {
      if (cancelled) return;
      setAppDetail(sp);
      setAppCp(cp);
      setTemplates(Array.isArray(tpls) ? tpls : []);
      setLibraryPackages(filterWorkspaceVisibleLibraryPackages(Array.isArray(packs) ? packs : [], scope?.workspaceId)
        .filter(model => model.library_package !== false && getManifestScopeKind(model.manifest ?? {}) === 'track'));
      setLibraryLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open, isEdit, appId, scope?.workspaceId, libraryRevision]);

  useEffect(() => {
    if (!open || isEdit) return;
    if (appId && manifestTracks.length > 0) {
      setOperationalModelChoice(`manifest:${manifestTracks[0].key}`);
    } else {
      setOperationalModelChoice('');
    }
  }, [open, isEdit, appId, manifestTracks]);

  useEffect(() => {
    if (!open || isEdit || appId) {
      setParentAppOptions([]);
      setChosenParentAppId('');
      return;
    }
    let cancelled = false;
    appsApi
      .list()
      .then(list => {
        if (!cancelled) setParentAppOptions(Array.isArray(list) ? list : []);
      })
      .catch(() => {
        if (!cancelled) setParentAppOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, isEdit, appId, scope?.workspaceId]);

  useEffect(() => {
    if (open && !isEdit) {
      setVisibilityChoice('private');
    }
  }, [open, isEdit]);

  useEffect(() => {
    if (editTrack) {
      setTitle(editTrack.title || '');
      setPurpose(editTrack.purpose || editTrack.description || '');
      setVisibilityChoice(normalizeTrackVisibility(editTrack.visibility));
    }
  }, [editTrack]);

  const selectedLibraryModel = libraryPackages.find(model => operationalModelChoice === `library:${model.id}`);
  const selectedAppSource = appSources.find(source => source.value === operationalModelChoice);
  const selectedName = selectedLibraryModel?.name || selectedAppSource?.name || 'Blank track';
  const selectedDescription = selectedLibraryModel?.description || selectedAppSource?.description
    || 'Start with the standard fields and add your own structure as you go.';
  const selectedCounts = selectedLibraryModel
    ? summarizeLibraryManifest(selectedLibraryModel.manifest ?? {}) : null;

  const continueToDetails = () => {
    if (operationalModelChoice && !title.trim()) setTitle(selectedName);
    if (operationalModelChoice && !purpose.trim()) {
      setPurpose(selectedLibraryModel?.description || selectedAppSource?.description || '');
    }
    setStep('details');
  };

  const inheritHint = 'This existing Track uses legacy inherited visibility. It is preserved until you choose Private, Workspace, or Public.';

  const applyOperationalModelToBody = (body: CreateTrackBody) => {
    const c = operationalModelChoice;
    if (c.startsWith('manifest:')) {
      body.app_track_type_key = c.slice('manifest:'.length);
    } else if (c.startsWith('template:')) {
      body.app_track_template_operational_model_id = c.slice('template:'.length);
    } else if (c.startsWith('library:')) {
      body.library_operational_model_id = c.slice('library:'.length);
    }
  };

  const handleSubmit = async () => {
    if (!title.trim()) {
      showToast('Title required', 'error');
      return;
    }
    setLoading(true);
    try {
      let t: Track;
      if (isEdit && editTrack) {
        t = await tracksApi.update(editTrack.id, {
          title: title.trim(),
          purpose: purpose.trim(),
          ...(visibilityChoice !== 'inherit' ? { visibility: visibilityChoice } : {}),
        });
      } else {
        const effectiveAppId = appId || chosenParentAppId || '';
        const body: CreateTrackBody = {
          title: title.trim(),
          purpose: purpose.trim(),
          ...(scope?.workspaceId ? { workspace_id: scope.workspaceId } : {}),
          ...(effectiveAppId ? { app_id: effectiveAppId } : {}),
        };
        applyOperationalModelToBody(body);
        body.visibility = visibilityChoice === 'inherit' ? 'private' : visibilityChoice;
        t = await tracksApi.create(body);
      }
      void invalidateWorkspaceListCaches(queryClient);
      await onCreated(t);
      onClose();
      setTitle('');
      setPurpose('');
      setVisibilityChoice('private');
      setOperationalModelChoice('');
      setChosenParentAppId('');
      showToast(
        isEdit ? 'Track updated!' : 'Track created!',
        'success',
        { label: 'View track', href: trackPath(t.id) },
      );
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to save track',
        'error'
      );
    } finally {
      setLoading(false);
    }
  };

  const deleteLibraryModel = async (model: OperationalModelNode) => {
    if (!await confirm({
      title: 'Delete operational model?',
      message: `Permanently delete “${model.name}” from the Track library? This removes its saved fields, entry types, tags, and views, so it can no longer be used to create new Tracks. Existing Tracks, Entries, and attachments created from it will remain unchanged. Models attached by reference cannot be deleted until detached. This action cannot be undone. You would need to upload or recreate the model to use it again.`,
      confirmLabel: 'Delete model', variant: 'danger',
    })) return;
    setDeletingModels(previous => new Set(previous).add(model.id));
    try {
      await operationalModelsApi.delete(model.id);
      setLibraryPackages(previous => previous.filter(item => item.id !== model.id));
      setOperationalModelChoice(previous => previous === `library:${model.id}` ? '' : previous);
      showToast('Operational model deleted.', 'success');
    } catch (error: unknown) {
      const detail = (error as { response?: { data?: { detail?: string | { message?: string } } }; message?: string });
      const reason = detail.response?.data?.detail;
      showToast((typeof reason === 'string' ? reason : reason?.message) || detail.message || 'Failed to delete operational model.', 'error');
    } finally {
      setDeletingModels(previous => { const next = new Set(previous); next.delete(model.id); return next; });
    }
  };

  const detailsBody = (
    <>
      <div>
        <label htmlFor="track-name" className="text-sm font-medium text-[var(--text)] mb-1.5 block">
          Track Name *
        </label>
        <Input
          id="track-name"
          className="w-full"
          value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder="e.g. Product Roadmap, Team Updates..."
          autoFocus
        />
      </div>
      <div>
        <label htmlFor="track-purpose" className="text-sm font-medium text-[var(--text)] mb-1.5 block">
          Purpose / Description
        </label>
        <Textarea
          id="track-purpose"
          value={purpose}
          onChange={e => setPurpose(e.target.value)}
          placeholder="What is this track for?"
          rows={3}
          noResize
        />
      </div>
      {!isEdit && !appId && parentAppOptions.length > 0 ? (
        <div>
          <label
            htmlFor="track-parent-app"
            className="text-sm font-medium text-[var(--text)] mb-1.5 block"
          >
            Parent App <span className="text-[var(--text-subtle)] font-normal">(optional)</span>
          </label>
          <AppSelect
            value={chosenParentAppId}
            onValueChange={v => setChosenParentAppId(v)}
            options={[
              { value: '', label: 'Standalone (no parent App)' },
              ...parentAppOptions.map(a => ({
                value: a.id,
                label: a.name?.trim() || 'Untitled app',
              })),
            ]}
            aria-label="Parent App"
            className="app-input"
          />
          <p className="mt-1 text-xs text-[var(--text-subtle)]">
            Tracks under an App inherit its collaborators and content
            profile. Leave standalone to manage access directly on the
            track.
          </p>
        </div>
      ) : null}
      <div className="rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel-2)]/25 p-4 space-y-3">
        <label className="text-sm font-medium text-[var(--text)] block">Visibility</label>
        <VisibilityField
          value={visibilityChoice}
          onChange={setVisibilityChoice}
          workspaceAvailable={!!scope?.workspaceId}
          inheritHint={inheritHint}
        />
      </div>
    </>
  );

  // Edit mode — single step via FormDialog.
  if (isEdit) {
    return (
      <FormDialog
        open={open}
        onClose={onClose}
        title="Edit Track"
        onSubmit={handleSubmit}
        submitLabel="Save Changes"
        submitLoading={loading}
        submitDisabled={!title.trim()}
      >
        {detailsBody}
      </FormDialog>
    );
  }

  // App-provided Track types retain their existing creation path. Standalone
  // Tracks get the same library/import/blank starting points as Manage Apps.
  return <>
    <StepDialog
      open={open}
      onClose={() => { if (!loading) onClose(); }}
      title="Manage Tracks"
      size={!appId && step === 'profile' ? 'wide' : 'form'}
      steps={['Choose a starting point', 'Track details']}
      activeIndex={step === 'profile' ? 0 : 1}
    >
      {step === 'profile' ? <>
        <Modal.Body noPadding noSpacing>
          <div className={appId ? 'p-5 sm:p-6' : 'grid min-w-0 sm:grid-cols-[minmax(0,1fr)_19rem]'}>
            <section aria-label="Track library" className={appId ? 'space-y-4' : 'min-w-0 space-y-4 p-5 sm:p-6'}>
              {!appId && <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <Text variant="body" weight="medium">Start with a model or create your own</Text>
                  <Text variant="body-sm" tone="muted" as="p" className="mt-1">
                    Tracks can live directly in {activeWorkspace?.name || 'your workspace'}. No App is required.
                  </Text>
                </div>
                {scope?.workspaceId && <Button variant="outline" size="sm"
                  icon={<Upload size={16} strokeWidth={LINE_ICON_STROKE} />}
                  onClick={() => setImportOpen(true)}>Import YAML</Button>}
              </div>}
              {libraryLoading && <div role="status"><Text variant="body-sm" tone="muted" as="p">Loading operational models…</Text></div>}
              {libraryError && <div role="alert" className="space-y-2">
                <Text variant="body-sm" tone="danger">{libraryError}</Text>
                <Button variant="outline" size="sm" onClick={() => setLibraryRevision(value => value + 1)}>Retry library</Button>
              </div>}
              <OperationalModelPicker
                value={operationalModelChoice}
                onChange={setOperationalModelChoice}
                defaultLabel={appId ? 'App defaults' : 'Blank track'}
                defaultDescription={appId ? 'Use the App baseline without an extra model.' : 'Use standard fields, then build the structure you need.'}
                appSources={appSources}
                libraryPackages={showLibraryRow ? libraryPackages : []}
                scopeFilter="track"
                onDelete={model => void deleteLibraryModel(model)}
                deletingIds={deletingModels}
              />
            </section>
            {!appId && <Surface as="aside" tone="panel-2" padding="lg" radius="none"
              className="min-w-0 space-y-4 border-t border-[var(--panel-border)] sm:border-l sm:border-t-0"
              aria-label="Track starting point">
              <div>
                <Text variant="label">Your starting point</Text>
                <Text variant="body" weight="medium" as="p" className="mt-3">{selectedName}</Text>
                <Text variant="body-sm" tone="muted" as="p" className="mt-2">{selectedDescription}</Text>
              </div>
              {selectedCounts && <Text variant="body-sm" tone="muted" as="p">
                {selectedCounts.entryTypeCount} entry types · {selectedCounts.viewCount} views
              </Text>}
              <Text variant="body-sm" tone="subtle" as="p">Next, name your track and choose who can see it. Nothing is created until you confirm.</Text>
            </Surface>}
          </div>
        </Modal.Body>
        <Modal.Footer align="between">
          {!appId ? <Button variant="secondary" className="min-h-11"
            icon={<Plus size={16} strokeWidth={LINE_ICON_STROKE} />}
            onClick={() => { setOperationalModelChoice(''); setStep('details'); }}>Create your own track</Button>
            : <Button variant="ghost" onClick={onClose}>Cancel</Button>}
          <Button variant="primary" onClick={continueToDetails}>Continue</Button>
        </Modal.Footer>
      </> : <>
        <Modal.Body>
          {!appId && <Text variant="body-sm" tone="muted" as="p">Starting point: {selectedName}</Text>}
          {detailsBody}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="ghost" disabled={loading} onClick={() => setStep('profile')}>Back</Button>
          <Button variant="primary" loading={loading} disabled={!title.trim()} onClick={handleSubmit}>Create track</Button>
        </Modal.Footer>
      </>}
    </StepDialog>
    {!appId && scope?.workspaceId && <ImportPackageModal key={scope.workspaceId}
      open={open && importOpen} workspaceId={scope.workspaceId} expectedScope="track"
      onClose={() => setImportOpen(false)}
      onSuccess={result => {
        const model = result.operational_model;
        if (model && getManifestScopeKind(model.manifest ?? {}) === 'track') {
          setLibraryPackages(previous => [...previous.filter(item => item.id !== model.id), model]);
          setOperationalModelChoice(`library:${model.id}`);
        }
        setImportOpen(false);
        setLibraryRevision(value => value + 1);
        showToast('Operational model imported. Review the track details before creating it.', 'success');
      }} />}
  </>;
}
