import { useCallback, useMemo, useState } from 'react';
import { Download, FileText } from 'lucide-react';

import { attachmentsApi } from '../../api/attachments';
import { useToast } from '../../context/ToastContext';
import type { Attachment, Entry } from '../../types';
import { formatAttachmentSize } from '../../utils/attachmentMime';
import { LINE_ICON_STROKE } from '../ui/IconWell';
import { AttachmentPane } from './attachments/AttachmentPane';
import { EntryDocumentEditor } from './EntryDocumentEditor';

/** The attachment id(s) a file field holds, whatever shape the value has. */
function fileFieldIds(value: unknown): string[] {
  if (typeof value === 'string') return value ? [value] : [];
  if (Array.isArray(value)) return value.flatMap(fileFieldIds);
  if (value && typeof value === 'object') {
    const id = (value as { id?: unknown; attachment_id?: unknown });
    return fileFieldIds(id.id ?? id.attachment_id);
  }
  return [];
}

/**
 * The file beside an entry (the entry "canvas").
 *
 * Shows the attachment the entry's file field points at, or the newest file
 * when the field is empty, with the same viewers the attachment dialog uses.
 * Older files stay selectable, so a re-render does not lose the earlier one.
 * The parent refetches the entry and its attachments; this pane only renders.
 */
export function EntryCanvasPane({
  entry,
  attachments,
  fileField,
  editable = false,
  canEdit = false,
  onBodySaved,
  trackId,
}: {
  entry: Entry;
  attachments: Attachment[];
  fileField?: string;
  /** Show the document editor tab (entry type ``canvas.editor``). */
  editable?: boolean;
  canEdit?: boolean;
  onBodySaved?(entry: Entry): void;
  trackId?: string;
}) {
  const { showToast } = useToast();
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [tab, setTab] = useState<'document' | 'file'>(editable ? 'document' : 'file');

  const files = useMemo(
    () =>
      attachments
        .filter(a => a.source_type !== 'url')
        .slice()
        .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')),
    [attachments]
  );

  const current = useMemo(() => {
    const picked = pickedId ? files.find(a => a.id === pickedId) : undefined;
    if (picked) return picked;
    const fieldValue = fileField
      ? (entry.custom_fields as Record<string, unknown> | undefined)?.[fileField]
      : undefined;
    for (const id of fileFieldIds(fieldValue)) {
      const hit = files.find(a => a.id === id);
      if (hit) return hit;
    }
    return files[0];
  }, [pickedId, files, fileField, entry.custom_fields]);

  const download = useCallback(
    async (attachment: Attachment) => {
      try {
        const { blob, contentType } = await attachmentsApi.fetchDownloadBlob(
          attachment.id
        );
        const url = URL.createObjectURL(
          contentType ? new Blob([blob], { type: contentType }) : blob
        );
        const a = document.createElement('a');
        a.href = url;
        a.download = attachment.filename || 'file';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 5000);
      } catch (e) {
        showToast(e instanceof Error ? e.message : 'Download failed', 'error');
      }
    },
    [showToast]
  );

  const fileView = !current ? (
      <div
        data-testid="entry-canvas-empty"
        className="flex h-full min-h-[320px] flex-col items-center justify-center gap-2 rounded-[var(--radius-card)] border border-dashed border-[var(--panel-border)] bg-[var(--panel)] px-6 text-center"
      >
        <FileText
          size={32}
          strokeWidth={LINE_ICON_STROKE}
          className="text-[var(--text-muted)]"
        />
        <div className="text-sm font-medium text-[var(--text)]">No file yet</div>
        <div className="text-xs text-[var(--text-muted)]">
          The file shows here once it is created. Use the buttons on this page,
          or ask the assistant.
        </div>
      </div>
    ) : (
    <div
      data-testid="entry-canvas"
      className="flex h-full min-h-[420px] flex-col overflow-hidden rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel)]"
    >
      <div className="flex items-center justify-between gap-2 border-b border-[var(--panel-border)] px-3 py-2 text-xs">
        <div className="min-w-0">
          {files.length > 1 ? (
            <select
              aria-label="File version"
              value={current.id}
              onChange={e => setPickedId(e.target.value)}
              className="max-w-full truncate rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-2 py-1 text-[var(--text)]"
            >
              {files.map((a, i) => (
                <option key={a.id} value={a.id}>
                  {i === 0 ? 'Latest: ' : ''}
                  {a.filename || 'file'}
                  {a.created_at ? ` (${new Date(a.created_at).toLocaleString()})` : ''}
                </option>
              ))}
            </select>
          ) : (
            <span className="truncate font-medium text-[var(--text)]">
              {current.filename || 'file'}
            </span>
          )}
          {formatAttachmentSize(current.size) ? (
            <span className="ml-2 text-[var(--text-muted)]">
              {formatAttachmentSize(current.size)}
            </span>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => void download(current)}
          className="inline-flex shrink-0 items-center gap-1 rounded-[var(--radius-input)] px-2 py-1 text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <Download size={12} strokeWidth={LINE_ICON_STROKE} />
          Download
        </button>
      </div>
      <div className="relative min-h-0 flex-1 overflow-auto bg-[var(--panel-2)]/30">
        <AttachmentPane key={current.id} attachment={current} onDownload={download} />
      </div>
    </div>
    );

  if (!editable) return fileView;

  // The file is out of date when the entry changed after it was made (a render
  // updates the entry a moment after attaching, so allow for that).
  const stale =
    !!current?.created_at &&
    !!entry.updated_at &&
    Date.parse(entry.updated_at) - Date.parse(current.created_at) > 10000;

  const tabClass = (active: boolean) =>
    `px-3 py-1.5 text-xs font-medium border-b-2 ${
      active
        ? 'border-[var(--brand-accent)] text-[var(--text)]'
        : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]'
    }`;

  return (
    <div className="flex h-full min-h-[420px] flex-col overflow-hidden rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel)]">
      <div role="tablist" className="flex items-center border-b border-[var(--panel-border)] px-1">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'document'}
          className={tabClass(tab === 'document')}
          onClick={() => setTab('document')}
        >
          Document
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'file'}
          className={tabClass(tab === 'file')}
          onClick={() => setTab('file')}
        >
          File{files.length ? ` (${files.length})` : ''}
        </button>
        {tab === 'file' && stale ? (
          <span
            data-testid="entry-canvas-stale"
            className="ml-auto pr-3 text-xs text-[var(--text-muted)]"
          >
            Edited since this file was made. Press Render file.
          </span>
        ) : null}
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'document' ? (
          <EntryDocumentEditor
            entry={entry}
            canEdit={canEdit}
            onSaved={updated => onBodySaved?.(updated)}
            trackId={trackId}
          />
        ) : (
          <div className="h-full p-0">{fileView}</div>
        )}
      </div>
    </div>
  );
}
