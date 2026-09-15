import { useState, useEffect, useMemo, useCallback} from 'react';
import { useParams } from 'react-router-dom';
import {
  Plus, CheckCircle2,
  HelpCircle, Loader2, MessageSquare, Pencil,
  PanelRightOpen, PanelRightClose,
} from 'lucide-react';
import { publicSharingApi, type PublicTrackPayload } from '../api/sharing';
import type { Entry, EntryTypeNode } from '../types';
import { Button } from '../components/ui/Button';
import { Modal } from '../components/ui/Modal';
import {
  CommentsPanel,
  COMMENT_FOOTER_CLASS,
} from '../components/entries/comments/CommentsPanel';
import { CommentComposer } from '../components/entries/comments/CommentComposer';
import { useToast } from '../context/ToastContext';
import { ViewRenderer } from '../views';
import { ViewTabs, LINE_ICON_STROKE, MarkdownContent } from '../components/ui';
import { IconButton } from '../ui';
import { useSidePanelRoom } from '../hooks/useSidePanelRoom';
import { Text, Surface, Input, Textarea, Select } from '../ui';
import { SeamlessField } from '../components/entries/SeamlessField';
import { EntryMetaFields } from '../components/entries/EntryMetaFields';
import { normalizeEntry } from '../api/helpers';
import { humanizeEnumValue } from '../utils/humanizeFieldKey';
import { buildBaseSlotPlaceholder } from '../utils/fieldPlaceholders';
import {
  slug,
  filterEntryTypeSlugsForView,
  resolveCreateDefaultEntryType
} from '../components/entries/entryFormCustomFields';
import { getMissingRequiredFields } from '../utils/entryMetaFields';
import { publicEntryAffordances } from '../utils/publicTrackAffordances';
import {
  resolveKanbanGroupBy,
  resolveKanbanWriteFieldKey,
  shouldRouteKanbanQuickAddToCompose
} from '../components/views/kanbanColumnUtils';
import type { ContentProfileFieldSpec } from '../types';

export function SharedTrackPage() {
  const { token = '' } = useParams<{ token: string }>();
  const toast = useToast();

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<PublicTrackPayload | null>(null);
  const [notFound, setNotFound] = useState(false);

  // Entries & Search State
  const [entries, setEntries] = useState<Entry[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(false);

  // Active View State
  const [activeView, setActiveView] = useState<any | null>(null);

  // Form State (Google Form Mode or Create Modal)
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedEntryType, setSelectedEntryType] = useState<any>(null);
  const [formTitle, setFormTitle] = useState('');
  const [formBody, setFormBody] = useState('');
  const [formCustomFields, setFormCustomFields] = useState<Record<string, any>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submittedSuccess, setSubmittedSuccess] = useState(false);

  /* One open entry, like the authenticated dialog. `editMode` is a mode of
     that dialog rather than a second piece of state holding its own entry:
     the previous pair — one for the form, one for the thread — could
     disagree, which is how closing the form left a discussion behind as a
     dialog that opened itself. */
  const [openEntry, setOpenEntry] = useState<any>(null);
  const [editMode, setEditMode] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [editTitle, setEditTitle] = useState('');
  const [editBody, setEditBody] = useState('');
  const [editCustomFields, setEditCustomFields] = useState<Record<string, any>>({});

  // Comments State
  const [comments, setComments] = useState<any[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [newCommentText, setNewCommentText] = useState('');
  const [postingComment, setPostingComment] = useState(false);

  // Relation Choices State
  const [relationChoices, setRelationChoices] = useState<Record<string, any[]>>({});
  const [relationLoading, setRelationLoading] = useState(false);

  // Validation Error State
  const [invalidFieldKey, setInvalidFieldKey] = useState<string | null>(null);

  // Wizard Step State
  const [currentStepIndex, setCurrentStepIndex] = useState(0);

  /* Below `sm` there is no room beside the dialog, so the panel renders under
     the form instead of vanishing — the same reachability fix the
     authenticated entry dialog needed. Declared with the other hooks: this
     component early-returns for loading / not-found / Google-Form mode, and a
     hook after those returns changes the hook count between renders. */
  const showSideColumn = useSidePanelRoom();

  // Dynamic Base Field configs for selected entry type (Creation Form)
  const selectedBaseFields = selectedEntryType?.form_schema?.base_fields || {};
  const selectedTitleBase = selectedBaseFields.title || {};
  const selectedBodyBase = selectedBaseFields.body || {};
  const selectedTitleEnabled = selectedTitleBase.enabled !== false;
  const selectedBodyEnabled = selectedBodyBase.enabled !== false;
  const selectedTitleLabel = String(selectedTitleBase.label || '').trim() || 'Title';
  const selectedBodyLabel = String(selectedBodyBase.label || '').trim() || 'Description';

  const selectedTitlePlaceholder = useMemo(() => buildBaseSlotPlaceholder({
    label: selectedTitleLabel,
    placeholder: String(selectedTitleBase.placeholder || ''),
    canonicalLabel: 'Title',
    slot: 'title'
  }), [selectedTitleLabel, selectedTitleBase.placeholder]);

  const selectedBodyPlaceholder = useMemo(() => buildBaseSlotPlaceholder({
    label: selectedBodyLabel,
    placeholder: String(selectedBodyBase.placeholder || ''),
    canonicalLabel: 'Body',
    slot: 'body'
  }), [selectedBodyLabel, selectedBodyBase.placeholder]);

  // Dynamic Base Field configs for editing entry type (Edit Form)
  const openEntryType = useMemo(() => {
    if (!openEntry || !data?.entry_types) return null;
    return data.entry_types.find((et: any) => et.id === openEntry.type_id) || null;
  }, [openEntry, data?.entry_types]);

  const openEntryFields = useMemo(
    () =>
      (openEntryType?.form_schema?.fields ?? []) as unknown as ContentProfileFieldSpec[],
    [openEntryType],
  );

  const editBaseFields = openEntryType?.form_schema?.base_fields || {};
  const editTitleBase = editBaseFields.title || {};
  const editBodyBase = editBaseFields.body || {};
  const editTitleEnabled = editTitleBase.enabled !== false;
  const editBodyEnabled = editBodyBase.enabled !== false;
  const editTitleLabel = String(editTitleBase.label || '').trim() || 'Title';
  const editBodyLabel = String(editBodyBase.label || '').trim() || 'Description';

  const editTitlePlaceholder = useMemo(() => buildBaseSlotPlaceholder({
    label: editTitleLabel,
    placeholder: String(editTitleBase.placeholder || ''),
    canonicalLabel: 'Title',
    slot: 'title'
  }), [editTitleLabel, editTitleBase.placeholder]);

  const editBodyPlaceholder = useMemo(() => buildBaseSlotPlaceholder({
    label: editBodyLabel,
    placeholder: String(editBodyBase.placeholder || ''),
    canonicalLabel: 'Body',
    slot: 'body'
  }), [editBodyLabel, editBodyBase.placeholder]);

  // Compute wizard steps dynamically based on field groups
  const formSteps = useMemo(() => {
    if (!selectedEntryType) return [];

    const groups: Record<string, any[]> = {};
    const groupOrder: string[] = [];

    const addFieldToGroup = (groupName: string, fieldItem: any) => {
      const normalizedName = groupName.trim();
      if (!groups[normalizedName]) {
        groups[normalizedName] = [];
        groupOrder.push(normalizedName);
      }
      groups[normalizedName].push(fieldItem);
    };

    // 1. Add Title if enabled (defaults to "Personal Details")
    if (selectedTitleEnabled) {
      addFieldToGroup("Personal Details", { type: 'base_title' });
    }

    // 2. Add custom fields grouped by their group key
    const fields = selectedEntryType.form_schema?.fields || [];
    for (const f of fields) {
      const gName = f.group ? String(f.group).trim() : "Personal Details";
      addFieldToGroup(gName, { type: 'custom', spec: f });
    }

    // 3. Add Body if enabled
    if (selectedBodyEnabled) {
      addFieldToGroup("Additional Info", { type: 'base_body' });
    }

    // Sort groups so "Personal Details" is always first
    const sortedGroupOrder = [...groupOrder];
    const pdIndex = sortedGroupOrder.indexOf("Personal Details");
    if (pdIndex > 0) {
      sortedGroupOrder.splice(pdIndex, 1);
      sortedGroupOrder.unshift("Personal Details");
    }

    return sortedGroupOrder.map(name => ({
      id: name.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      title: name,
      fields: groups[name]
    }));
  }, [selectedEntryType, selectedTitleEnabled, selectedBodyEnabled]);

  // Validate the inputs in the current wizard step
  const validateCurrentStep = () => {
    const currentStep = formSteps[currentStepIndex];
    if (!currentStep) return true;

    for (const fieldItem of currentStep.fields) {
      if (fieldItem.type === 'base_title' && selectedTitleEnabled && !formTitle.trim()) {
        toast.showToast(`${selectedTitleLabel} is required`, 'error');
        setInvalidFieldKey('title');
        return false;
      }
      if (fieldItem.type === 'custom' && fieldItem.spec.required) {
        const val = formCustomFields[fieldItem.spec.key];
        if (val === undefined || val === null || (typeof val === 'string' && !val.trim())) {
          toast.showToast(`${fieldItem.spec.name} is required`, 'error');
          setInvalidFieldKey(fieldItem.spec.key);
          return false;
        }
      }
    }
    return true;
  };

  const handleNextStep = () => {
    if (validateCurrentStep()) {
      setInvalidFieldKey(null);
      setCurrentStepIndex(prev => Math.min(prev + 1, formSteps.length - 1));
    }
  };

  const handlePrevStep = () => {
    setInvalidFieldKey(null);
    setCurrentStepIndex(prev => Math.max(prev - 1, 0));
  };

  // Render a progress stepper at the top of paged forms
  const renderWizardProgress = (steps: any[], currentIndex: number) => {
    if (steps.length <= 1) return null;
    return (
      <div className="mb-6 animate-fade-in">
        <div className="flex justify-between items-center uppercase tracking-wider mb-2">
          <Text variant="meta" weight="semibold" tone="muted">Step {currentIndex + 1} of {steps.length}</Text>
          <Text variant="meta" weight="bold" tone="inherit" className="text-[var(--brand-accent-fg)]">{steps[currentIndex].title}</Text>
        </div>
        <div className="h-1.5 w-full bg-[var(--panel-border)] rounded-full overflow-hidden">
          <div
            className="h-full bg-[var(--brand-accent-fg)] transition-all duration-300 ease-out"
            style={{ width: `${((currentIndex + 1) / steps.length) * 100}%` }}
          />
        </div>
        <div className="flex justify-between mt-3 px-1">
          {steps.map((step, idx) => (
            <div key={step.id} className="flex flex-col items-center flex-1 relative">
              <Surface
                tone={idx > currentIndex ? 'panel-2' : 'transparent'}
                border={idx > currentIndex ? 'default' : 'none'}
                radius="none"
                className={`w-3.5 h-3.5 rounded-full flex items-center justify-center text-[8px] font-bold transition-all duration-200 ${
                  idx === currentIndex
                    ? 'bg-[var(--brand-accent-fg)] text-[var(--bg)] ring-4 ring-[var(--brand-accent-fg)]/20'
                    : idx < currentIndex
                    ? 'bg-emerald-500 text-white'
                    : ''
                }`}
              >
                {idx > currentIndex ? (
                  <Text variant="meta" tone="muted" weight="bold" className="text-[8px] leading-none">{idx + 1}</Text>
                ) : (
                  idx < currentIndex ? '✓' : idx + 1
                )}
              </Surface>
              <Text
                variant="meta"
                weight={idx === currentIndex ? 'semibold' : 'medium'}
                tone={idx === currentIndex ? 'default' : 'muted'}
                className="sm:text-xs mt-2 text-center px-1 leading-tight max-w-[64px] sm:max-w-[120px] truncate sm:whitespace-normal sm:break-words"
              >
                {step.title}
              </Text>
            </div>
          ))}
        </div>
      </div>
    );
  };

  // Render fields belonging to the current step
  const renderStepFields = (
    step: any,
    values: Record<string, any>,
    onChange: (key: string, val: any) => void,
    variant: 'modal' | 'form'
  ) => {
    if (!step?.fields) return null;
    const rowsCount = variant === 'form' ? 4 : 3;

    return step.fields.map((fieldItem: any) => {
      if (fieldItem.type === 'base_title') {
        return (
          <div key="base-title" className="space-y-1.5">
            <Text as="label" variant="label" tone="muted" weight="semibold" className="block">
              {selectedTitleLabel} <span className="text-red-500">*</span>
            </Text>
            <Input
              type="text"
              required
              placeholder={selectedTitlePlaceholder}
              value={formTitle}
              onChange={e => {
                setFormTitle(e.target.value);
                if (invalidFieldKey === 'title') setInvalidFieldKey(null);
              }}
              invalid={invalidFieldKey === 'title'}
              size={variant === 'form' ? 'md' : 'sm'}
            />
          </div>
        );
      }

      if (fieldItem.type === 'base_body') {
        return (
          <div key="base-body" className="space-y-1.5">
            <Text as="label" variant="label" tone="muted" weight="semibold" className="block">
              {selectedBodyLabel}
            </Text>
            <Textarea
              rows={rowsCount}
              placeholder={selectedBodyPlaceholder}
              value={formBody}
              onChange={e => {
                setFormBody(e.target.value);
                if (invalidFieldKey === 'body') setInvalidFieldKey(null);
              }}
              invalid={invalidFieldKey === 'body'}
              size={variant === 'form' ? 'md' : 'sm'}
            />
          </div>
        );
      }

      const f = fieldItem.spec;
      const isInvalid = invalidFieldKey === f.key;
      return (
        <div key={f.key} className="space-y-1">
          <div className={isInvalid ? "rounded-[var(--radius-input)] border border-red-500/80 p-0.5" : ""}>
            <SeamlessField
              field={f}
              value={values[f.key]}
              onChange={val => {
                onChange(f.key, val);
                if (isInvalid) setInvalidFieldKey(null);
              }}
              relationChoices={relationChoices[f.key]}
              relationLoading={relationLoading}
            />
          </div>
        </div>
      );
    });
  };

  // Load Track Details
  const loadTrack = useCallback(async () => {
    try {
      setLoading(true);
      const res = await publicSharingApi.getPublicTrack(token);

      // Move "post" or "Post" to the end of the entry types list so it's not the default
      if (res.entry_types && res.entry_types.length > 0) {
        const sortedTypes = [...res.entry_types].sort((a, b) => {
          const aName = (a.name || '').toLowerCase();
          const bName = (b.name || '').toLowerCase();
          if (aName === 'post') return 1;
          if (bName === 'post') return -1;
          return 0;
        });
        res.entry_types = sortedTypes;
        setSelectedEntryType(sortedTypes[0]);
      }

      setData(res);
      if (res.views && res.views.length > 0) {
        const defView = res.views.find((v: any) => v.is_default) || res.views[0];
        setActiveView(defView);
      }
    } catch {
      setNotFound(true);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadTrack();
  }, [loadTrack]);

  // Load Entries (if allowed)
  const loadEntries = useCallback(async () => {
    if (!data?.public_permissions?.read_entries) return;
    try {
      setEntriesLoading(true);
      const res = await publicSharingApi.getPublicTrackEntries(token);
      const normalized = (res.entries || []).map((e: any) => normalizeEntry(e));
      setEntries(normalized);
    } catch {
      toast.showToast('Failed to load entries', 'error');
    } finally {
      setEntriesLoading(false);
    }
  }, [token, data, toast]);

  useEffect(() => {
    if (data?.public_permissions?.read_entries) {
      loadEntries();
    }
  }, [data, loadEntries]);

  // Load Comments
  const loadComments = useCallback(async (entryId: string) => {
    if (!data?.public_permissions?.read_comments) return;
    try {
      setCommentsLoading(true);
      const res = await publicSharingApi.getPublicComments(token, entryId);
      setComments(res.comments || []);
    } catch {
      toast.showToast('Failed to load comments', 'error');
    } finally {
      setCommentsLoading(false);
    }
  }, [token, data, toast]);

  useEffect(() => {
    if (openEntry && data?.public_permissions?.read_comments) {
      loadComments(openEntry.id);
    }
  }, [openEntry, data?.public_permissions?.read_comments, loadComments]);

  // Relation Choices Fetch — one call per (entry_type, relation field) via the
  // token-scoped relation-options endpoint. Candidate resolution (same-track
  // vs. cross-track sibling tracks under the shared track's App) happens
  // server-side per the field's target_entry_types/target_track_types/
  // allow_cross_track config; the public entries list alone can't cover a
  // relation that targets a DIFFERENT track (e.g. Performance -> Content
  // Pipeline), which is what caused "Content Piece has no available options".
  useEffect(() => {
    if (!data?.entry_types) return;
    const relationFieldRefs: Array<{ entryTypeId: string; field: any }> = [];
    for (const et of data.entry_types) {
      const fields = et.form_schema?.fields || [];
      for (const f of fields) {
        if (f.type === 'relation') relationFieldRefs.push({ entryTypeId: et.id, field: f });
      }
    }
    if (relationFieldRefs.length === 0) return;

    let active = true;
    setRelationLoading(true);
    Promise.all(
      relationFieldRefs.map(({ entryTypeId, field }) =>
        publicSharingApi
          .getPublicTrackRelationOptions(token, entryTypeId, field.key)
          .then(res => ({ key: field.key, targets: res.targets || [] }))
          // Must mirror the success shape, including optional track_title —
          // otherwise the two branches form a union without that property and
          // reading it below fails.
          .catch(() => ({
            key: field.key,
            targets: [] as Array<{
              id: string;
              title: string;
              track_title?: string;
            }>
          }))
      )
    )
      .then(results => {
        if (!active) return;
        const newChoices: Record<string, any[]> = {};
        for (const { key, targets } of results) {
          newChoices[key] = targets.map(t => ({
            value: t.id,
            label: t.track_title ? `${t.title || t.id} (${t.track_title})` : (t.title || t.id)
          }));
        }
        setRelationChoices(newChoices);
      })
      .finally(() => {
        if (active) setRelationLoading(false);
      });
    return () => {
      active = false;
    };
  }, [data, token]);

  const trackEntryTypeFields = useMemo(() => {
    const seen = new Map<string, any>();
    const list = data?.entry_types || [];
    for (const et of list) {
      const fields = et.form_schema?.fields || [];
      for (const f of fields) {
        const key = typeof f.key === 'string' ? f.key : '';
        if (key && !seen.has(key)) seen.set(key, f);
      }
    }
    return Array.from(seen.values());
  }, [data]);

  // Filter entry types based on active view
  const filteredEntryTypes = useMemo(() => {
    if (!data?.entry_types) return [];
    if (!activeView) return data.entry_types;
    const allowedSlugs = filterEntryTypeSlugsForView(data.entry_types as any, activeView.entry_type_keys);
    return data.entry_types.filter((et: any) => {
      const etSlug = slug(et.name || et.key || '');
      return allowedSlugs.includes(etSlug);
    });
  }, [data?.entry_types, activeView]);

  // Update selected entry type when active view changes
  useEffect(() => {
    if (!data?.entry_types || !filteredEntryTypes.length) return;

    // Find the default entry type based on the view
    const defaultType = resolveCreateDefaultEntryType(
      filteredEntryTypes.map((et: any) => slug(et.name || et.key || '')),
      activeView?.entry_type_keys,
      activeView?.default_entry_type_key,
      data.track.content_profile_defaults?.default_entry_type
    );

    // Find the entry type object for the default slug
    const selected = filteredEntryTypes.find((et: any) =>
      slug(et.name || et.key || '') === defaultType
    ) || filteredEntryTypes[0];

    setSelectedEntryType(selected);
  }, [data, filteredEntryTypes, activeView]);

  // De-dupe saved views by (type + entry_type_keys) so the tab strip
  // never shows two literally-identical tabs, just like TrackDetailPage
  const dedupedTabViews = useMemo(() => {
    if (!data?.views) return [];
    const savedViews = data.views.filter(v => !v.hidden);
    const byKey = new Map<string, any>();
    for (const v of savedViews) {
      const keys = Array.isArray(v.entry_type_keys)
        ? [...v.entry_type_keys].map(s => String(s).toLowerCase().trim()).sort()
        : [];
      const dedupeKey = `${v.type}::${keys.join(',')}`;
      const existing = byKey.get(dedupeKey);
      if (!existing) {
        byKey.set(dedupeKey, v);
      } else if (v.is_default && !existing.is_default) {
        byKey.set(dedupeKey, v);
      } else if (
        v.is_default === existing.is_default &&
        v.name &&
        !existing.name
      ) {
        byKey.set(dedupeKey, v);
      }
    }
    return Array.from(byKey.values());
  }, [data]);

  const viewTabOptions = useMemo(() => {
    return dedupedTabViews.map(v => ({
      value: v.id,
      label: v.name || v.type || v.view_type
    }));
  }, [dedupedTabViews]);

  // Keep activeView in sync with deduped views
  useEffect(() => {
    if (!dedupedTabViews.length) return;
    if (!activeView || !dedupedTabViews.some(v => v.id === activeView.id)) {
      const defView = dedupedTabViews.find(v => v.is_default) || dedupedTabViews[0];
      setActiveView(defView);
    }
  }, [dedupedTabViews, activeView]);

  const submitButtonLabel = useMemo(() => {
    if (!data) return 'Submit Response';
    const slug =
      activeView?.default_entry_type_key ||
      activeView?.entry_type_keys?.[0] ||
      data.track.content_profile_defaults?.default_entry_type ||
      data.entry_types?.[0]?.name ||
      'entry';
    const label = humanizeEnumValue(slug);
    return label ? `New ${label}` : 'New entry';
  }, [data, activeView]);

  // Handle Submit Form
  const handleSubmitEntry = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedEntryType) return;

    // Validate the current step first
    if (!validateCurrentStep()) return;

    // Validate all wizard steps to ensure no required fields are missing
    for (let stepIdx = 0; stepIdx < formSteps.length; stepIdx++) {
      const step = formSteps[stepIdx];
      for (const fieldItem of step.fields) {
        if (fieldItem.type === 'base_title' && selectedTitleEnabled && !formTitle.trim()) {
          toast.showToast(`${selectedTitleLabel} is required`, 'error');
          setInvalidFieldKey('title');
          setCurrentStepIndex(stepIdx);
          return;
        }
        if (fieldItem.type === 'custom' && fieldItem.spec.required) {
          const val = formCustomFields[fieldItem.spec.key];
          if (val === undefined || val === null || (typeof val === 'string' && !val.trim())) {
            toast.showToast(`${fieldItem.spec.name} is required`, 'error');
            setInvalidFieldKey(fieldItem.spec.key);
            setCurrentStepIndex(stepIdx);
            return;
          }
        }
      }
    }

    setSubmitting(true);
    setInvalidFieldKey(null);
    try {
      await publicSharingApi.createPublicEntry(token, {
        title: selectedTitleEnabled ? formTitle : 'Untitled',
        type_id: selectedEntryType.id,
        body: selectedBodyEnabled ? formBody : '',
        custom_fields: formCustomFields
      });
      setSubmittedSuccess(true);
      toast.showToast('Response submitted successfully', 'success');
      setFormTitle('');
      setFormBody('');
      setFormCustomFields({});
      setCurrentStepIndex(0);
      setShowCreateModal(false);
      loadEntries();
    } catch (err: any) {
      toast.showToast(err.message || 'Failed to submit entry', 'error');
      const match = /(?:Field|Relation field) '([^']+)'/i.exec(err.message || '');
      if (match) {
        // Find which step the invalid field belongs to and switch to it
        setInvalidFieldKey(match[1]);
        for (let stepIdx = 0; stepIdx < formSteps.length; stepIdx++) {
          const hasField = formSteps[stepIdx].fields.some(
            (fi: any) => fi.type === 'custom' && fi.spec.key === match[1]
          );
          if (hasField) {
            setCurrentStepIndex(stepIdx);
            break;
          }
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  // Handle Edit Entry
  const handleUpdateEntry = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!openEntry) return;
    if (editTitleEnabled && !editTitle.trim()) {
      toast.showToast(`${editTitleLabel} is required`, 'error');
      return;
    }

    setSubmitting(true);
    setInvalidFieldKey(null);
    try {
      await publicSharingApi.updatePublicEntry(token, openEntry.id, {
        title: editTitleEnabled ? editTitle : openEntry.title,
        body: editBodyEnabled ? editBody : openEntry.body,
        custom_fields: editCustomFields
      });
      toast.showToast('Entry updated successfully', 'success');
      setEditMode(false);
      setOpenEntry((prev: any) =>
        prev ? { ...prev, title: editTitle, body: editBody, custom_fields: editCustomFields } : prev,
      );
      loadEntries();
    } catch (err: any) {
      toast.showToast(err.message || 'Failed to update entry', 'error');
      const match = /(?:Field|Relation field) '([^']+)'/i.exec(err.message || '');
      if (match) {
        setInvalidFieldKey(match[1]);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const closeEntryDialog = () => {
    setOpenEntry(null);
    setEditMode(false);
  };

  /* Opening a record starts read-first, the way the authenticated dialog
     does. Edit is a control inside it rather than a different destination,
     so the row click means one thing under every permission combination. */
  const openEntryDialog = (entry: any) => {
    setOpenEntry(entry);
    setEditMode(false);
    setEditTitle(entry.title || '');
    setEditBody(entry.body || '');
    setEditCustomFields(entry.custom_fields || {});
  };

  // Handle Submit Comment. No form event: the shared CommentComposer sends
  // on Enter and on the send button, and a nested <form> inside the entry
  // dialog is invalid HTML that breaks the dialog's own submission.
  const submitPublicComment = async () => {
    if (!openEntry || !newCommentText.trim()) return;

    setPostingComment(true);
    try {
      await publicSharingApi.createPublicComment(token, openEntry.id, newCommentText);
      setNewCommentText('');
      loadComments(openEntry.id);
      toast.showToast('Comment posted', 'success');
    } catch (err: any) {
      toast.showToast(err.message || 'Failed to post comment', 'error');
    } finally {
      setPostingComment(false);
    }
  };

  // Render dynamic form inputs based on EntryType schema using SeamlessField
  const renderFormFields = (
    entryType: any,
    values: Record<string, any>,
    onChange: (key: string, val: any) => void
  ) => {
    if (!entryType?.form_schema?.fields) return null;
    return entryType.form_schema.fields.map((f: any) => {
      const isInvalid = invalidFieldKey === f.key;
      return (
        <div key={f.key} className="space-y-1">
          <div className={isInvalid ? "rounded-[var(--radius-input)] border border-red-500/80 p-0.5" : ""}>
            <SeamlessField
              field={f}
              value={values[f.key]}
              onChange={val => {
                onChange(f.key, val);
                if (isInvalid) setInvalidFieldKey(null);
              }}
              relationChoices={relationChoices[f.key]}
              relationLoading={relationLoading}
            />
          </div>
        </div>
      );
    });
  };

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--bg)] gap-3">
        <Loader2 className="animate-spin text-[var(--brand-accent-fg)]" size={24} />
        <Text variant="body" tone="muted">Loading public track…</Text>
      </div>
    );
  }

  if (notFound || !data) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--bg)] p-6 text-center">
        <Surface tone="panel" radius="card" elevation="pop" className="max-w-md w-full p-8">
          <HelpCircle size={48} className="mx-auto animate-bounce" style={{ color: 'var(--text-muted)' }} />
          <Text variant="heading-lg" weight="bold" as="h1" className="block mt-4">Not Available</Text>
          <Text variant="body" tone="muted" as="p" className="block mt-2 leading-relaxed">
            This public link is invalid, has expired, or has been revoked by the owner.
          </Text>
        </Surface>
      </div>
    );
  }

  const perms = data.public_permissions;
  const affordances = publicEntryAffordances(perms);
  const track = data.track;
  const isGoogleFormMode = !perms.read_entries && perms.create_entries;

  // GOOGLE FORM LAYOUT MODE
  if (isGoogleFormMode) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-[var(--bg)] via-[var(--bg)] to-[var(--panel-3)]/10 py-12 px-4 flex flex-col justify-center items-center">
        <Surface tone="panel" radius="card" elevation="pop" className="max-w-xl w-full overflow-hidden relative">

          {/* Subtle Accent Glow */}
          <div
            className="h-1.5 w-full"
            style={{ backgroundColor: track.accent_color || 'var(--brand-accent-fg)' }}
          />

          <div className="p-6 sm:p-8 space-y-6">

            {/* Header info */}
            <div className="space-y-2 border-b border-[var(--panel-border)] pb-5">
              <div className="flex items-center gap-3">
                <Text variant="heading-lg" weight="bold" as="h1">{track.title}</Text>
              </div>
              {track.purpose && (
                <Text variant="body-sm" tone="muted" as="p" className="block leading-relaxed">
                  {track.purpose}
                </Text>
              )}
            </div>

            {submittedSuccess ? (
              <div className="text-center py-8 space-y-4 animate-fade-in">
                <CheckCircle2 size={56} className="mx-auto text-emerald-500 animate-pulse" />
                <div>
                  <Text variant="heading-md" weight="semibold" as="h2">Response Recorded</Text>
                  <Text variant="body-sm" tone="muted" as="p" className="block mt-1">
                    Thank you! Your response has been submitted successfully to the track.
                  </Text>
                </div>
                <Button
                  variant="outline"
                  onClick={() => setSubmittedSuccess(false)}
                  className="mt-2"
                >
                  Submit another response
                </Button>
              </div>
            ) : (
              <form onSubmit={handleSubmitEntry} className="space-y-5">
                {/* Entry Type selector if multiple types exist */}
                {filteredEntryTypes.length > 1 && (
                  <div className="space-y-1.5">
                    <Text as="label" variant="label" tone="muted" weight="semibold" className="block">
                      Response Type
                    </Text>
                    <Select
                      value={selectedEntryType?.id || ''}
                      onChange={e => {
                        const et = filteredEntryTypes.find(t => t.id === e.target.value);
                        if (et) {
                          setSelectedEntryType(et);
                          setFormCustomFields({});
                          setCurrentStepIndex(0);
                        }
                      }}
                    >
                      {filteredEntryTypes.map(et => (
                        <option key={et.id} value={et.id}>
                          {et.name}
                        </option>
                      ))}
                    </Select>
                  </div>
                )}

                {/* Progress Indicators if wizard has multiple steps */}
                {renderWizardProgress(formSteps, currentStepIndex)}

                {/* Render fields for the current active step */}
                <div className="space-y-4">
                  {formSteps[currentStepIndex] && renderStepFields(
                    formSteps[currentStepIndex],
                    formCustomFields,
                    (k, v) => {
                      setFormCustomFields(prev => ({ ...prev, [k]: v }));
                    },
                    'form'
                  )}
                </div>

                <div className="pt-4 border-t border-[var(--panel-border)] flex justify-between items-center">
                  <div>
                    {formSteps.length > 1 && currentStepIndex > 0 && (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={handlePrevStep}
                      >
                        Back
                      </Button>
                    )}
                  </div>
                  <div className="flex gap-2">
                    {formSteps.length > 1 && currentStepIndex < formSteps.length - 1 ? (
                      <Button
                        key="wizard-next-btn"
                        type="button"
                        variant="primary"
                        onClick={handleNextStep}
                      >
                        Next
                      </Button>
                    ) : (
                      <Button
                        key="wizard-submit-btn"
                        type="submit"
                        variant="primary"
                        disabled={submitting}
                      >
                        {submitting ? 'Submitting…' : 'Submit'}
                      </Button>
                    )}
                  </div>
                </div>
              </form>
            )}
          </div>
        </Surface>
      </div>
    );
  }


  /* Public comment thread. Rendered BESIDE the entry dialog when one is open
     (the standard placement — the record stays visible while you read and
     reply) and as a standalone right-hand surface when the link grants no
     edit rights, where there is no dialog to sit beside. */
  const publicCommentsPanel = openEntry && perms.read_comments ? (
          <Surface tone="panel" border="none" radius="none" className="w-full h-full flex flex-col relative">
            {/* Same ViewTabs strip the authenticated entry dialog uses, so the
                public surface reads as the same product rather than a
                separate one. A public link exposes discussion only — no
                attachments or audit trail — so it is a single tab, kept as a
                tab (not a bare heading) for that consistency. */}
            <ViewTabs
              options={[
                {
                  value: 'comments',
                  label: 'Comments',
                  icon: <MessageSquare size={14} strokeWidth={LINE_ICON_STROKE} />,
                  count: comments.length,
                },
              ]}
              value="comments"
              onChange={() => {}}
              size="sm"
              ariaLabel="Entry details"
              className="shrink-0 px-2"
            />

            {/* The same thread widget the signed-in entry dialog renders.
                A public visitor cannot reply, edit or moderate — those
                capabilities are absent rather than restyled, so a comment
                reads identically on both sides of the login. */}
            <div className="flex-1 overflow-y-auto overscroll-contain px-4 py-3">
              <CommentsPanel
                comments={comments}
                loading={commentsLoading}
                user={null}
                canComment={!!perms.create_comments}
                canReply={false}
                canModerate={false}
              />
            </div>

            {perms.create_comments ? (
              <div className={COMMENT_FOOTER_CLASS}>
                <CommentComposer
                  value={newCommentText}
                  onChange={setNewCommentText}
                  onSubmit={() => void submitPublicComment()}
                  submitting={postingComment}
                  mentions={false}
                />
              </div>
            ) : (
              <div className={COMMENT_FOOTER_CLASS}>
                <Text variant="body-sm" tone="subtle" className="block text-center">
                  Commenting is turned off for this link.
                </Text>
              </div>
            )}
          </Surface>
  ) : null;

  // INTERACTIVE VIEWS LAYOUT MODE
  return (
    <div className="min-h-screen bg-[var(--bg)] flex flex-col">
      {/* Top Header Bar */}
      <Surface as="header" tone="panel" border="none" radius="none" className="border-b border-[var(--panel-border)] sticky top-0 z-30 shadow-sm backdrop-blur-md bg-opacity-95">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div>
              <Text variant="body-lg" weight="bold" as="h1">{track.title}</Text>
              {data.workspace?.name && (
                <Text variant="meta" tone="subtle" weight="semibold" as="p" className="block uppercase tracking-wider">{data.workspace.name}</Text>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {perms.create_entries && (
              <Button
                variant="primary"
                size="sm"
                icon={<Plus size={14} />}
                onClick={() => {
                  setSubmittedSuccess(false);
                  setShowCreateModal(true);
                  setCurrentStepIndex(0);
                }}
              >
                {submitButtonLabel}
              </Button>
            )}
          </div>
        </div>
      </Surface>

      {/* View Tabs */}
      {viewTabOptions.length > 0 && activeView && (
        <Surface tone="panel" border="none" radius="none" className="border-b border-[var(--panel-border)]">
          <div className="max-w-6xl mx-auto px-4">
            <ViewTabs
              options={viewTabOptions}
              value={activeView.id}
              onChange={nextId => {
                const next = data.views.find(v => v.id === nextId);
                if (next) setActiveView(next);
              }}
              ariaLabel="Track views"
              noBorder
            />
          </div>
        </Surface>
      )}

      {/* Main Body */}
      {/* `public-view-fill` (index.css) stretches the rendered view into the
          height `flex-1` already reserves here. Without it a short track left
          the view's card floating above a viewport-tall band of empty page. */}
      <main className="max-w-6xl mx-auto w-full px-4 py-6 flex-1 flex flex-col animate-fade-in public-view-fill">
        {activeView ? (
          <ViewRenderer
            view={activeView}
            entries={entries}
            isLoading={entriesLoading}
            onEntryOpen={e => {
              // One destination. Every capability the link grants is a
              // control inside the dialog, so no permission combination can
              // change what a row click means or take another one's place.
              if (affordances.rowAction === 'open') openEntryDialog(e);
            }}
            onEntryDelete={undefined}
            onEntryDeleteFailed={undefined}
            onEntryEdit={perms.update_entries ? (e => {
              // A view with its own edit affordance opens straight into the
              // form; the dialog is the same one either way.
              openEntryDialog(e);
              setEditMode(true);
            }) : undefined}
            onEntryUpdate={perms.update_entries ? (async (updated) => {
              try {
                await publicSharingApi.updatePublicEntry(token, updated.id, {
                  title: updated.title,
                  body: updated.body,
                  custom_fields: updated.custom_fields
                });
                toast.showToast('Entry updated successfully', 'success');
                loadEntries();
              } catch (err: any) {
                toast.showToast(err.message || 'Failed to update entry', 'error');
              }
            }) : undefined}
            onEntryPersist={perms.update_entries ? (async (updated) => {
              try {
                await publicSharingApi.updatePublicEntry(token, updated.id, {
                  title: updated.title,
                  body: updated.body,
                  custom_fields: updated.custom_fields
                });
                loadEntries();
              } catch (err: any) {
                toast.showToast(err.message || 'Failed to update entry', 'error');
              }
            }) : undefined}
            onViewUpdate={undefined}
            onKanbanColumnEnumSync={undefined}
            onEntryCreate={perms.create_entries ? (async (input) => {
              // Kanban quick-add on the public page sends only the card title
              // plus the column's group value (e.g. {status: "draft"}). If the
              // target entry type has OTHER required fields the quick-add can't
              // supply (e.g. Content Piece's required "format"/"platform"), a
              // blind create 400s with "Field 'format' is required". Mirror the
              // authenticated TrackDetailPage behavior: route to the full create
              // modal pre-seeded with the title + column value so the visitor can
              // fill the remaining required fields, instead of erroring out.
              // Resolve the SAME entry type the widget is posting (it passes
              // input.type as a slug), not the page-level selectedEntryType —
              // those can differ (e.g. a stale "Post" type still lingering on
              // the track vs. the kanban's real "Content Piece"), and using the
              // wrong one would read an empty field list and skip the guard.
              const inputTypeSlug = input.type ? slug(String(input.type)) : '';
              const targetType =
                (inputTypeSlug &&
                  filteredEntryTypes.find(
                    (et: any) => slug(et.name || et.key || '') === inputTypeSlug
                  )) ||
                filteredEntryTypes.find(
                  (et: any) => et.id === selectedEntryType?.id
                ) ||
                selectedEntryType ||
                filteredEntryTypes[0];
              const typeFields = ((targetType?.form_schema?.fields ??
                []) as ContentProfileFieldSpec[]);
              const seeded = input.custom_fields ?? {};
              const missingRequired = getMissingRequiredFields(typeFields, seeded);
              let routeToCompose = missingRequired.length > 0;
              if (!routeToCompose && activeView?.type === 'kanban') {
                const groupBy = resolveKanbanGroupBy(
                  (activeView.config as Record<string, unknown> | undefined)
                    ?.group_by
                );
                const groupWriteFieldKey = resolveKanbanWriteFieldKey(
                  groupBy,
                  typeFields
                );
                routeToCompose = shouldRouteKanbanQuickAddToCompose(
                  typeFields,
                  groupWriteFieldKey,
                  seeded
                );
              }
              if (routeToCompose) {
                if (targetType) setSelectedEntryType(targetType);
                setFormTitle(input.title || '');
                setFormBody('');
                setFormCustomFields({ ...seeded });
                setCurrentStepIndex(0);
                setInvalidFieldKey(null);
                setShowCreateModal(true);
                return;
              }
              try {
                await publicSharingApi.createPublicEntry(token, {
                  title: input.title,
                  type_id: targetType?.id || filteredEntryTypes[0]?.id,
                  custom_fields: input.custom_fields
                });
                toast.showToast('Entry created successfully', 'success');
                loadEntries();
              } catch (err: any) {
                toast.showToast(err.message || 'Failed to create entry', 'error');
              }
            }) : undefined}
            entryTypeSlugs={filteredEntryTypes.map(et => et.name ?? et.key ?? '')}
            entryTypes={filteredEntryTypes.map(
              (et): EntryTypeNode => ({
                id: et.id,
                name: et.name ?? et.key ?? et.id,
                form_schema: et.form_schema as unknown as EntryTypeNode['form_schema']
              }),
            )}
            trackDefaultEntryTypeKey={data.track.content_profile_defaults?.default_entry_type}
            fields={trackEntryTypeFields}
            filterType=""
            onFilterChange={() => {}}
            fetchMore={undefined}
            hasNextPage={false}
            isFetchingNext={false}
            isEditor={perms.create_entries || perms.update_entries}
            publicPermissions={perms}
            publicToken={token}
            track={track as any}
          />
        ) : (
          <div className="py-12 text-center">
            <Text variant="body-sm" tone="muted">No views configured.</Text>
          </div>
        )}
      </main>

      {/* Create runs through the same shell as everything else. It was the
          third hand-rolled overlay on this page, with the same missing
          dialog semantics as the edit one. */}
      {showCreateModal && (
        <Modal
          open
          onClose={() => setShowCreateModal(false)}
          title={submitButtonLabel}
        >
            <form onSubmit={handleSubmitEntry} className="p-5 space-y-4">
              {filteredEntryTypes.length > 1 && (
                <div className="space-y-1.5">
                  <Text as="label" variant="label" tone="muted" weight="semibold" className="block">Entry Type</Text>
                  <Select
                    size="sm"
                    value={selectedEntryType?.id || ''}
                    onChange={e => {
                      const et = filteredEntryTypes.find(t => t.id === e.target.value);
                      if (et) {
                        setSelectedEntryType(et);
                        setFormCustomFields({});
                        setCurrentStepIndex(0);
                      }
                    }}
                  >
                    {filteredEntryTypes.map(et => (
                      <option key={et.id} value={et.id}>
                        {et.name}
                      </option>
                    ))}
                  </Select>
                </div>
              )}

              {/* Progress Indicators if wizard has multiple steps */}
              {renderWizardProgress(formSteps, currentStepIndex)}

              {/* Render fields for the current active step */}
              <div className="space-y-4">
                {formSteps[currentStepIndex] && renderStepFields(
                  formSteps[currentStepIndex],
                  formCustomFields,
                  (k, v) => {
                    setFormCustomFields(prev => ({ ...prev, [k]: v }));
                  },
                  'modal'
                )}
              </div>

              <div className="pt-4 border-t border-[var(--panel-border)] flex justify-between items-center">
                <div>
                  {formSteps.length > 1 && currentStepIndex > 0 ? (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={handlePrevStep}
                    >
                      Back
                    </Button>
                  ) : (
                    <Button variant="outline" type="button" onClick={() => setShowCreateModal(false)}>
                      Cancel
                    </Button>
                  )}
                </div>
                <div className="flex gap-2">
                  {formSteps.length > 1 && currentStepIndex < formSteps.length - 1 ? (
                    <Button
                      key="modal-next-btn"
                      type="button"
                      variant="primary"
                      onClick={handleNextStep}
                    >
                      Next
                    </Button>
                  ) : (
                    <Button
                      key="modal-submit-btn"
                      variant="primary"
                      type="submit"
                      disabled={submitting}
                    >
                      {submitting ? 'Submitting…' : 'Submit'}
                    </Button>
                  )}
                </div>
              </div>
            </form>
        </Modal>
      )}

      {/* The same `Modal` shell the authenticated entry dialog uses. This was
          a hand-rolled `fixed inset-0` overlay: no `role="dialog"`, no
          `aria-modal`, no focus trap, no scroll lock and no Escape-to-close,
          all of which the shell provides — measured on the live public page
          before this change. It also named itself after the action rather
          than the record, where the signed-in dialog shows the record's own
          title, and closed with a bare text glyph. */}
      {openEntry && (
        <Modal
          open
          onClose={closeEntryDialog}
          title={openEntry.title || 'Entry'}
          sidePanel={
            showSideColumn && panelOpen && publicCommentsPanel
              ? publicCommentsPanel
              : undefined
          }
          /* Same surface as the authenticated dialog: the thread belongs to
             it whether it sits beside the record or under it. */
          hasCompanionPanel={Boolean(publicCommentsPanel)}
          headerActions={
            <div className="flex items-center gap-1.5">
              {/* Edit is a mode of this dialog, not a separate destination —
                  the same pencil-then-form shape the authenticated dialog
                  uses. */}
              {affordances.canEditEntries && !editMode && (
                <IconButton
                  label="Edit entry"
                  title="Edit entry"
                  size="md"
                  onClick={() => setEditMode(true)}
                >
                  <Pencil size={16} strokeWidth={LINE_ICON_STROKE} aria-hidden />
                </IconButton>
              )}
              {showSideColumn && publicCommentsPanel && (
                <IconButton
                  label={panelOpen ? 'Hide details panel' : 'Show details panel'}
                  title={panelOpen ? 'Hide details panel' : 'Show details panel'}
                  size="md"
                  onClick={() => setPanelOpen(o => !o)}
                  aria-expanded={panelOpen}
                >
                  {panelOpen ? (
                    <PanelRightClose size={16} strokeWidth={LINE_ICON_STROKE} aria-hidden />
                  ) : (
                    <PanelRightOpen size={16} strokeWidth={LINE_ICON_STROKE} aria-hidden />
                  )}
                </IconButton>
              )}
            </div>
          }
        >
            {!editMode ? (
              /* Read-first, like the authenticated dialog. This surface used
                 to open straight into a form titled after the action, so a
                 visitor with edit rights could not simply look at a record —
                 and one without them had no way to see its fields at all.
                 `EntryMetaFields` is the same read view the signed-in dialog
                 renders, driven by the entry type's `form_schema.fields`,
                 which the public payload already carries. */
              <div className="p-5">
                <EntryMetaFields
                  fields={openEntryFields}
                  values={(openEntry.custom_fields || {}) as Record<string, unknown>}
                  variant="detail"
                  readOnly
                />
                {openEntry.body ? (
                  /* Same renderer the authenticated dialog uses. Plain text
                     here showed the source instead of the document — the
                     seed's "## What this App does" and "- **Ideas**" reached
                     a public reader as literal markdown. */
                  <div className="mt-4">
                    <MarkdownContent>{openEntry.body}</MarkdownContent>
                  </div>
                ) : null}
              </div>
            ) : (
            <form onSubmit={handleUpdateEntry} className="p-5 space-y-4">
              {editTitleEnabled && (
                <div className="space-y-1.5">
                  <Text as="label" variant="label" tone="muted" weight="semibold" className="block">
                    {editTitleLabel} <span className="text-red-500">*</span>
                  </Text>
                  <Input
                    type="text"
                    required
                    placeholder={editTitlePlaceholder}
                    value={editTitle}
                    onChange={e => {
                      setEditTitle(e.target.value);
                      if (invalidFieldKey === 'title') setInvalidFieldKey(null);
                    }}
                    invalid={invalidFieldKey === 'title'}
                    size="sm"
                  />
                </div>
              )}

              {editBodyEnabled && (
                <div className="space-y-1.5">
                  <Text as="label" variant="label" tone="muted" weight="semibold" className="block">{editBodyLabel}</Text>
                  <Textarea
                    rows={3}
                    placeholder={editBodyPlaceholder}
                    value={editBody}
                    onChange={e => {
                      setEditBody(e.target.value);
                      if (invalidFieldKey === 'body') setInvalidFieldKey(null);
                    }}
                    invalid={invalidFieldKey === 'body'}
                    size="sm"
                  />
                </div>
              )}

              {renderFormFields(
                data.entry_types.find(et => et.id === openEntry.type_id),
                editCustomFields,
                (k, v) => {
                  setEditCustomFields(prev => ({ ...prev, [k]: v }));
                }
              )}

              <div className="pt-4 border-t border-[var(--panel-border)] flex justify-end gap-2">
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => {
                    if (openEntry) {
                      setEditTitle(openEntry.title || '');
                      setEditBody(openEntry.body || '');
                      setEditCustomFields(openEntry.custom_fields || {});
                    }
                    setEditMode(false);
                  }}
                >
                  Cancel
                </Button>
                <Button variant="primary" type="submit" disabled={submitting}>
                  {submitting ? 'Saving…' : 'Save Changes'}
                </Button>
              </div>
            </form>
            )}
            {/* Mobile host: below `sm` the shell drops its side column, so the
                thread renders under the fields rather than disappearing.
                Sits OUTSIDE the edit <form> — nesting forms is invalid HTML
                and breaks submission. */}
            {publicCommentsPanel && !showSideColumn ? (
              <div className="sm:hidden border-t border-[var(--panel-border)] flex flex-col">
                {publicCommentsPanel}
              </div>
            ) : null}
        </Modal>
      )}

    </div>
  );
}
