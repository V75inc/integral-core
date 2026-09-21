import { useState, useEffect, useRef, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Modal } from '../ui/Modal';
import { Button, ColorPicker, VisibilityField } from '../ui';
import type { VisibilityChoice } from '../ui';
import { Input, Textarea } from '../../ui';
import { FormDialog, StepDialog } from '../../templates';
import { appsApi, operationalModelsApi } from '../../api';
import { invalidateWorkspaceListCaches } from '../../queryKeys';
import { useToast } from '../../context/ToastContext';
import { useScope } from '../../context/ScopeContext';
import type { OperationalModelNode, App } from '../../types';
import { parseTrackAccentHex } from '../../utils';
import { appPath } from '../../utils/resourcePaths';
import { OperationalModelPicker } from '../library/OperationalModelPicker';
import { IncludeSeedDataToggle } from './IncludeSeedDataToggle';
import { countManifestSeedEntries } from '../../utils/manifestSeeds';

function normalizeAppVisibility(v: string | undefined): VisibilityChoice {
  if (!v) return 'private';
  if (v === 'team' || v === 'organization') return 'workspace';
  if (v === 'inherit') return 'inherit';
  if (v === 'private' || v === 'workspace' || v === 'public') return v;
  return 'private';
}

interface AppModalProps {
  open: boolean;
  onClose(): void;
  onSaved(app: App): void;
  editApp?: App | null;
  /** Skip profile picker — create a blank app with no library package. */
  mode?: 'create' | 'blank' | 'edit';
}

export function AppModal({ open, onClose, onSaved, editApp, mode }: AppModalProps) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const { scope } = useScope();
  const isEdit = mode === 'edit' || !!editApp;
  const isBlank = mode === 'blank';

  const [name, setName] = useState(editApp?.name || '');
  const [description, setDescription] = useState(editApp?.description || '');
  const [visibilityChoice, setVisibilityChoice] = useState<VisibilityChoice>(() =>
    isEdit ? normalizeAppVisibility(editApp?.visibility) : 'inherit'
  );
  const [accentColor, setAccentColor] = useState<string | null>(
    editApp?.accent_color ? parseTrackAccentHex(editApp.accent_color) : null
  );
  const [colorOpen, setColorOpen] = useState(false);
  const colorBtnRef = useRef<HTMLButtonElement>(null);
  const [popoverStyle, setPopoverStyle] = useState<{ top: number; left: number } | null>(null);
  const [libraryPackageId, setLibraryPackageId] = useState('');

  function toggleColorPicker() {
    if (!colorOpen && colorBtnRef.current) {
      const r = colorBtnRef.current.getBoundingClientRect();
      setPopoverStyle({ top: r.bottom + 6, left: r.left });
    }
    setColorOpen(v => !v);
  }
  const [libraryPackages, setLibraryPackages] = useState<OperationalModelNode[]>([]);
  const [saving, setSaving] = useState(false);
  const [includeSeedData, setIncludeSeedData] = useState(true);

  type AppModalStep = 'details' | 'profile';
  const [step, setStep] = useState<AppModalStep>('profile');

  useEffect(() => {
    if (!open) return;
    if (!isEdit && !isBlank) {
      operationalModelsApi
        .list()
        .then(setLibraryPackages)
        .catch(() => setLibraryPackages([]));
    }
  }, [open, isEdit, isBlank]);

  useEffect(() => {
    if (open) {
      setStep(isBlank ? 'details' : 'profile');
      setColorOpen(false);
    }
  }, [open, isBlank]);

  // Phase 33 — auto-populate Name + Description from the chosen library
  // bundle's display name + package description. Triggers ONLY when the
  // user has not yet typed into either field (so manual edits aren't
  // clobbered by switching profiles). The picker carries the human
  // display name on OperationalModelNode.name (set by library-sync from
  // package.name in the YAML); package.description still comes from the
  // manifest payload because the loader does not promote it onto the
  // CP node.
  useEffect(() => {
    if (isEdit) return;
    if (!libraryPackageId) return;
    const pkg = libraryPackages.find(p => p.id === libraryPackageId);
    if (!pkg) return;
    // Both name + description live on the OperationalModelNode (library-sync
    // copies from YAML package.name + package.description; the loader
    // strips them from manifest.package). Fall through to the manifest
    // block as a defensive backstop.
    const manifest =
      ((pkg as unknown) as { manifest?: Record<string, unknown> })?.manifest || {};
    const pkgBlock =
      ((manifest as { package?: Record<string, unknown> }).package) || {};
    const pkgDesc =
      ((pkg as unknown) as { description?: string })?.description || '';
    const defaultName = String(pkg.name || (pkgBlock as Record<string, unknown>).name || '');
    const defaultDescription = String(
      pkgDesc || (pkgBlock as Record<string, unknown>).description || '',
    );
    setName(prev => (prev.trim() ? prev : defaultName));
    setDescription(prev => (prev.trim() ? prev : defaultDescription));
  }, [isEdit, libraryPackageId, libraryPackages]);

  useEffect(() => {
    if (!open) return;
    if (editApp) {
      setName(editApp.name || '');
      setDescription(editApp.description || '');
      setVisibilityChoice(normalizeAppVisibility(editApp.visibility));
      setAccentColor(
        editApp.accent_color ? parseTrackAccentHex(editApp.accent_color) : null
      );
    } else {
      setName('');
      setDescription('');
      setVisibilityChoice('inherit');
      setAccentColor(null);
      setLibraryPackageId('');
      setIncludeSeedData(true);
    }
  }, [open, editApp]);

  const selectedPackageSeedCount = useMemo(() => {
    if (!libraryPackageId) return 0;
    const pkg = libraryPackages.find(p => p.id === libraryPackageId);
    const manifest =
      ((pkg as unknown) as { manifest?: Record<string, unknown> })?.manifest || {};
    return countManifestSeedEntries(manifest);
  }, [libraryPackageId, libraryPackages]);

  const handleSubmit = async () => {
    if (!name.trim()) {
      showToast('Name is required', 'error');
      return;
    }
    if (accentColor && !parseTrackAccentHex(accentColor)) {
      showToast('Identity color must be a #RGB or #RRGGBB hex value', 'error');
      return;
    }
    setSaving(true);
    try {
      const resolvedAccent = accentColor
        ? parseTrackAccentHex(accentColor) || ''
        : '';
      let sp: App;
      if (isEdit && editApp) {
        sp = await appsApi.update(editApp.id, {
          name: name.trim(),
          description: description.trim(),
          visibility: visibilityChoice,
          accent_color: resolvedAccent,
        });
      } else {
        sp = await appsApi.create({
          name: name.trim(),
          description: description.trim() || undefined,
          ...(scope?.workspaceId ? { workspace_id: scope.workspaceId } : {}),
          ...(visibilityChoice === 'inherit' ? {} : { visibility: visibilityChoice }),
          ...(libraryPackageId
            ? {
                library_operational_model_id: libraryPackageId,
                include_seed_data: includeSeedData,
              }
            : {}),
          ...(resolvedAccent ? { accent_color: resolvedAccent } : {}),
        });
      }
      void invalidateWorkspaceListCaches(queryClient);
      onSaved(sp);
      onClose();
      showToast(
        isEdit ? 'App updated' : 'App created',
        'success',
        { label: 'View app', href: appPath(sp.id) },
      );
    } catch (e: unknown) {
      showToast(
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || (isEdit ? 'Failed to update app' : 'Failed to create app'),
        'error'
      );
    } finally {
      setSaving(false);
    }
  };

  // Reusable Details step body (used by both edit FormDialog and create StepDialog step 2).
  const detailsBody = (
    <>
      <div>
        <label htmlFor="app-name" className="text-sm font-medium block mb-1.5">Name *</label>
        <div className="flex items-center gap-2.5">
          <button
            ref={colorBtnRef}
            type="button"
            aria-label="Identity color"
            title={accentColor ? `Color: ${accentColor}` : 'Choose identity color'}
            onClick={toggleColorPicker}
            className="h-8 w-8 shrink-0 rounded-full border-2 border-[var(--panel-border)] cursor-pointer transition-transform duration-fast hover:scale-110 focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)]"
            style={{ backgroundColor: accentColor ?? 'var(--badge-muted-bg)' }}
          />
          <Input
            id="app-name"
            className="flex-1"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Q4 Initiatives"
            autoFocus
          />
        </div>
        {colorOpen && popoverStyle && createPortal(
          <>
            <div className="fixed inset-0 z-popover" onClick={() => setColorOpen(false)} />
            <div
              className="fixed z-popover rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel)] p-3 shadow-[var(--shadow-lg)]"
              style={popoverStyle}
            >
              <ColorPicker
                value={accentColor}
                onChange={c => { setAccentColor(c); setColorOpen(false); }}
                size="sm"
                allowClear
              />
            </div>
          </>,
          document.body
        )}
      </div>
      <div>
        <label htmlFor="app-description" className="text-sm font-medium block mb-1.5">Description</label>
        <Textarea
          id="app-description"
          rows={2}
          noResize
          value={description}
          onChange={e => setDescription(e.target.value)}
        />
      </div>
      <div className="rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel-2)]/25 p-4 space-y-3">
        <label className="text-sm font-medium text-[var(--text)] block">
          Visibility
        </label>
        <VisibilityField
          value={visibilityChoice}
          onChange={setVisibilityChoice}
          workspaceAvailable={!!scope?.workspaceId}
          showInherit={true}
          inheritHint="Applies the active workspace's visibility."
        />
      </div>
      {!isEdit && libraryPackageId ? (
        <div className="rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel-2)]/25 p-4">
          <IncludeSeedDataToggle
            checked={includeSeedData}
            onChange={setIncludeSeedData}
            seedEntryCount={selectedPackageSeedCount}
          />
        </div>
      ) : null}
    </>
  );

  // Edit mode — single step, render via FormDialog.
  if (isEdit) {
    return (
      <FormDialog
        open={open}
        onClose={onClose}
        title="Edit app"
        onSubmit={handleSubmit}
        submitLabel="Save changes"
        submitLoading={saving}
        submitDisabled={!name.trim()}
      >
        {detailsBody}
      </FormDialog>
    );
  }

  // Create mode — blank app skips profile picker.
  if (isBlank) {
    return (
      <FormDialog
        open={open}
        onClose={onClose}
        title="Create blank app"
        onSubmit={handleSubmit}
        submitLabel="Create"
        submitLoading={saving}
        submitDisabled={!name.trim()}
      >
        {detailsBody}
      </FormDialog>
    );
  }

  // Create mode — two steps, render via StepDialog with per-step footers.
  return (
    <StepDialog
      open={open}
      onClose={onClose}
      title={step === 'profile' ? 'New app — Choose operational model' : 'New app — Details'}
      steps={['Choose operational model', 'Details']}
      activeIndex={step === 'profile' ? 0 : 1}
    >
      {step === 'profile' ? (
        <>
          <Modal.Body noSpacing>
            <OperationalModelPicker
              value={libraryPackageId ? `library:${libraryPackageId}` : ''}
              onChange={v => setLibraryPackageId(v.startsWith('library:') ? v.slice(8) : v)}
              defaultLabel="None / Default"
              defaultDescription="No operational model applied — app starts with base schema."
              libraryPackages={libraryPackages}
              scopeFilter="app"
            />
          </Modal.Body>
          <Modal.Footer align="between">
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => setStep('details')}>
              Next →
            </Button>
          </Modal.Footer>
        </>
      ) : (
        <>
          <Modal.Body>{detailsBody}</Modal.Body>
          <Modal.Footer>
            <Button variant="ghost" onClick={() => setStep('profile')}>
              ← Back
            </Button>
            <Button
              variant="primary"
              loading={saving}
              disabled={!name.trim()}
              onClick={handleSubmit}
            >
              Create
            </Button>
          </Modal.Footer>
        </>
      )}
    </StepDialog>
  );
}
