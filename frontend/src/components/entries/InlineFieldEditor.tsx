import { useState, useRef, useEffect, type KeyboardEvent } from 'react';
import { Pencil } from 'lucide-react';
import { LINE_ICON_STROKE } from '../ui';
import type { ContentProfileFieldSpec } from '../../types';

interface InlineFieldEditorProps {
  field: ContentProfileFieldSpec;
  value: unknown;
  readOnly?: boolean;
  onCommit: (newValue: unknown) => Promise<void>;
}

export function InlineFieldEditor({
  field,
  value,
  readOnly,
  onCommit,
}: InlineFieldEditorProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [commitError, setCommitError] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const committingRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  function startEdit() {
    if (readOnly) return;
    setDraft(value != null ? String(value) : '');
    setEditing(true);
    setCommitError(false);
  }

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  async function commit() {
    if (committingRef.current) return;
    committingRef.current = true;
    setSaving(true);
    try {
      const committed =
        field.type === 'number'
          ? draft === ''
            ? null
            : Number(draft)
          : draft === ''
          ? null
          : draft;
      await onCommit(committed);
      if (mountedRef.current) setEditing(false);
    } catch {
      if (mountedRef.current) setCommitError(true);
    } finally {
      committingRef.current = false;
      if (mountedRef.current) setSaving(false);
    }
  }

  function cancel() {
    setEditing(false);
    setCommitError(false);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      void commit();
    }
    if (e.key === 'Escape') cancel();
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        type={field.type === 'number' ? 'number' : 'text'}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => { if (!commitError) void commit(); }}
        disabled={saving}
        aria-label={`Edit ${field.name}`}
        className={[
          'w-full rounded px-1.5 py-0.5 text-sm border',
          'bg-[var(--panel)] text-[var(--text)]',
          'focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)]',
          commitError
            ? 'border-[var(--danger-fg)]'
            : 'border-[var(--brand-accent-line)]',
        ].join(' ')}
      />
    );
  }

  const displayValue =
    value != null && value !== '' ? (
      <span>{String(value)}</span>
    ) : (
      <span className="text-[var(--text-subtle)] italic">—</span>
    );

  return (
    <button
      type="button"
      onClick={startEdit}
      disabled={readOnly}
      aria-label={readOnly ? field.name : `Edit ${field.name}`}
      className={[
        'group flex items-center gap-1.5 w-full text-left rounded px-0.5 -mx-0.5',
        'text-sm text-[var(--text)]',
        readOnly ? 'cursor-default' : 'hover:bg-[var(--panel-2)] cursor-text',
        'transition-colors duration-fast',
      ].join(' ')}
    >
      <span className="min-w-0 flex-1">{displayValue}</span>
      {!readOnly && (
        <Pencil
          size={11}
          strokeWidth={LINE_ICON_STROKE}
          className="shrink-0 opacity-0 group-hover:opacity-60 transition-opacity text-[var(--text-muted)]"
          aria-hidden
        />
      )}
    </button>
  );
}
