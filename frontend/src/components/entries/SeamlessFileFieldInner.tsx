import { useEffect, useState } from 'react';
import { Paperclip, X } from 'lucide-react';

import type { OperationalModelFieldSpec } from '../../types';
import {
  attachmentsApi,
  type AttachmentRecord,
} from '../../api/attachments';
import { useToast } from '../../context/ToastContext';
import { LINE_ICON_STROKE } from '../ui/IconWell';

/**
 * Write-mode editor for ``file`` / ``files`` OperationalModel fields.
 *
 * Phase 4 contract: the file is uploaded to the entry first (via the
 * standard attachments endpoint), then the field's value is set to the
 * resulting attachment id(s). This component therefore needs the
 * entry id to manage uploads — passed via ``entryId`` so the field
 * can self-host its picker without the parent form pre-fetching
 * choice data (relation-style).
 *
 * Behaviour:
 *   - Renders the currently-bound attachment ids as chips with a
 *     remove affordance.
 *   - Offers an inline "Attach file…" button that opens the file
 *     picker, uploads the result against the entry, and appends the
 *     returned id to the field value.
 *   - Honours ``config.accept`` for the picker's ``accept`` attribute
 *     and ``config.max_count`` for cap enforcement.
 *   - Read-only mode hides the upload trigger but keeps chips visible.
 *
 * The parent caller (EntryFormExpanded / EntryDetail edit mode) is
 * expected to flush the resulting field value back to the entry via
 * the standard PATCH path — the backend validates that referenced
 * attachments are bound to the entry.
 */

interface SeamlessFileFieldInnerProps {
  field: OperationalModelFieldSpec;
  value: unknown;
  onChange(value: unknown): void;
  many: boolean;
  readonly: boolean;
  /**
   * Required for uploads to succeed. When the entry is still a draft
   * (no id yet), the parent should disable this component or render
   * a placeholder; we treat empty/missing as read-only.
   */
  entryId?: string;
}

interface UnifiedFileItem {
  id?: string;
  file?: File;
  name: string;
}

function getUnifiedFiles(value: unknown, labels: Record<string, string>): UnifiedFileItem[] {
  if (value == null || value === '') return [];
  const list = Array.isArray(value) ? value : [value];
  return list.map((item) => {
    if (item instanceof File) {
      return { file: item, name: item.name };
    }
    if (item && typeof item === 'object') {
      if ((item as any).file instanceof File) {
        return { file: (item as any).file, name: (item as any).name || (item as any).file.name };
      }
      if (typeof (item as any).id === 'string') {
        const id = (item as any).id;
        return { id, name: labels[id] || (item as any).name || id };
      }
    }
    const id = String(item);
    return { id, name: labels[id] || id };
  }).filter(Boolean);
}

export function SeamlessFileFieldInner({
  field,
  value,
  onChange,
  many,
  readonly,
  entryId,
}: SeamlessFileFieldInnerProps) {
  const { showToast } = useToast();
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState(false);
  const cfg = (field.config as Record<string, unknown> | undefined) ?? {};
  const accept = Array.isArray(cfg.accept) ? (cfg.accept as string[]) : [];
  const maxCount = typeof cfg.max_count === 'number'
    ? (cfg.max_count as number)
    : many
    ? 10
    : 1;

  const items = getUnifiedFiles(value, labels);
  const ids = items.map(x => x.id).filter(Boolean) as string[];

  // Light-touch fetch of filenames for whichever ids are currently
  // bound; runs once per id change so re-renders don't thrash.
  useEffect(() => {
    let cancelled = false;
    const missing = ids.filter((id) => !labels[id]);
    if (!missing.length) return;
    (async () => {
      const next: Record<string, string> = {};
      await Promise.all(
        missing.map(async (id) => {
          try {
            const detail = await attachmentsApi.get(id);
            const att = detail.attachment as AttachmentRecord;
            next[id] = att.filename || id;
          } catch {
            next[id] = id;
          }
        })
      );
      if (!cancelled) {
        setLabels((prev) => ({ ...prev, ...next }));
      }
    })();
    return () => {
      cancelled = true;
    };
    // ids is derived from value; tracking value is sufficient.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(ids)]);

  const remove = (itemToRemove: UnifiedFileItem) => {
    if (readonly) return;
    const nextItems = items.filter((item) => {
      if (itemToRemove.id && item.id) return item.id !== itemToRemove.id;
      if (itemToRemove.file && item.file) return item.file !== itemToRemove.file;
      return true;
    });
    const outputValues = nextItems.map(item => item.file || item.id).filter(Boolean);
    onChange(many ? outputValues : outputValues[0] ?? null);
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files || !files.length) return;
    const available = maxCount - items.length;
    if (available <= 0) {
      showToast(
        many
          ? `This field accepts at most ${maxCount} ${maxCount === 1 ? 'file' : 'files'}.`
          : 'This field accepts a single file. Remove it first to replace.',
        'error'
      );
      return;
    }
    const toUpload = Array.from(files).slice(0, available);

    if (entryId) {
      setUploading(true);
      const newIds: string[] = [];
      try {
        for (const file of toUpload) {
          const record = await attachmentsApi.uploadForEntry(entryId, file);
          if (record.id) {
            newIds.push(record.id);
            setLabels((prev) => ({
              ...prev,
              [record.id]: record.filename || record.id,
            }));
          }
        }
        if (newIds.length) {
          const existingIds = items.map(item => item.id).filter(Boolean) as string[];
          const merged = many ? [...existingIds, ...newIds] : newIds[0];
          onChange(merged);
        }
      } catch (e) {
        showToast(
          e instanceof Error ? e.message : 'Upload failed',
          'error'
        );
      } finally {
        setUploading(false);
      }
    } else {
      const nextItems = [...items];
      for (const file of toUpload) {
        nextItems.push({ file, name: file.name });
      }
      const outputValues = nextItems.map(item => item.file || item.id).filter(Boolean);
      onChange(many ? outputValues : outputValues[0] ?? null);
    }
  };

  return (
    <div className="space-y-1.5 pl-3">
      <p className="text-[12px] font-medium text-[var(--text-muted)]">
        {field.name} {field.required && <span className="text-red-500">*</span>}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {items.length === 0 && readonly && (
          <span className="text-xs italic text-[var(--text-subtle)]">No file</span>
        )}
        {items.map((item, index) => (
          <span
            key={item.id || `local-${index}-${item.name}`}
            className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-[var(--panel-border)] bg-[var(--panel-2)]/40 px-2 py-0.5 text-xs text-[var(--text)]"
          >
            <Paperclip
              size={11}
              strokeWidth={LINE_ICON_STROKE}
              className="text-[var(--text-muted)]"
            />
            <span className="max-w-[160px] truncate">
              {item.name}
            </span>
            {!readonly && (
              <button
                type="button"
                onClick={() => remove(item)}
                aria-label={`Remove ${item.name}`}
                className="text-[var(--text-muted)] hover:text-[var(--danger-fg)]"
              >
                <X size={10} strokeWidth={LINE_ICON_STROKE} />
              </button>
            )}
          </span>
        ))}
        {!readonly && items.length < maxCount && (
          <label className="inline-flex cursor-pointer items-center gap-1 rounded-[var(--radius-pill)] border border-dashed border-[var(--panel-border)] px-2 py-0.5 text-xs text-[var(--text-muted)] hover:text-[var(--text)]">
            <Paperclip size={11} strokeWidth={LINE_ICON_STROKE} />
            {uploading ? 'Uploading…' : 'Attach file'}
            <input
              type="file"
              multiple={many}
              accept={accept.join(',') || undefined}
              className="hidden"
              disabled={uploading}
              onChange={(e) => {
                const fs = e.target.files;
                e.target.value = '';
                void handleFiles(fs);
              }}
            />
          </label>
        )}
      </div>
    </div>
  );
}
