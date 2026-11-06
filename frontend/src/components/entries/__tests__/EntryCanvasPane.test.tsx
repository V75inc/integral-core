import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../api/attachments', () => ({ attachmentsApi: { fetchDownloadBlob: vi.fn() } }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ showToast: vi.fn() }) }));
vi.mock('../attachments/AttachmentPane', () => ({ AttachmentPane: () => <div data-testid="file-pane" /> }));
vi.mock('../EntryDocumentEditor', () => ({ EntryDocumentEditor: () => <div data-testid="text-editor" /> }));

import { EntryCanvasPane, prefersFilePreview } from '../EntryCanvasPane';

const file = { id: 'a1', filename: 'payslip.pdf', source_type: 'upload', created_at: '2027-05-01T10:00:00Z', size: 100 } as never;
const entry = (over: Record<string, unknown>) => ({ id: 'e1', custom_fields: {}, body: '', ...over }) as never;

describe('prefersFilePreview', () => {
  it('prefers the file when it is the master copy', () => {
    expect(prefersFilePreview(entry({ custom_fields: { edit_source: 'file' }, body: 'text' }), true)).toBe(true);
  });
  it('prefers the file when there is no text body', () => {
    expect(prefersFilePreview(entry({ body: '   ' }), true)).toBe(true);
  });
  it('prefers the text editor when the text is the master and has content', () => {
    expect(prefersFilePreview(entry({ custom_fields: { edit_source: 'text' }, body: 'Dear X' }), true)).toBe(false);
  });
  it('never prefers a file that does not exist', () => {
    expect(prefersFilePreview(entry({ body: '' }), false)).toBe(false);
  });
});

describe('EntryCanvasPane tabs', () => {
  it('opens a generated file on the preview with plain tab names, preview first', () => {
    render(<EntryCanvasPane entry={entry({ custom_fields: { edit_source: 'file' } })} attachments={[file]} editable canEdit />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map(t => t.textContent)).toEqual(['Preview', 'Edit text']);
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('file-pane')).toBeTruthy();
  });
  it('opens a text document on the text editor first', () => {
    render(<EntryCanvasPane entry={entry({ body: 'Dear X', custom_fields: { edit_source: 'text' } })} attachments={[file]} editable canEdit />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map(t => t.textContent)).toEqual(['Edit text', 'Preview']);
    expect(screen.getByTestId('text-editor')).toBeTruthy();
  });
});
