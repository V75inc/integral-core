import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { AttachmentRow } from '../attachments/AttachmentRow';
import { EntryDetailPageChrome } from '../EntryDetailPageChrome';

afterEach(cleanup);

describe('full-page entry utilities', () => {
  it('renders one details panel above the record without opening a dialog', () => {
    render(<EntryDetailPageChrome title="Document" onClose={() => {}}
      sidePanel={<div>Attachments and comments</div>}>
      <p>Saved record</p>
    </EntryDetailPageChrome>);
    expect(screen.getByRole('region', { name: 'Entry utilities' })).toBeVisible();
    expect(screen.getAllByText('Attachments and comments')).toHaveLength(1);
    expect(screen.getByText('Saved record')).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
  });

  it('respects hide/show without duplicating content or losing the record', () => {
    const props = { title: 'Document', onClose: () => {} };
    const { rerender } = render(<EntryDetailPageChrome {...props} sidePanel={<p>Files</p>}><p>Record</p></EntryDetailPageChrome>);
    rerender(<EntryDetailPageChrome {...props}><p>Record</p></EntryDetailPageChrome>);
    expect(screen.queryByTestId('entry-page-utilities')).not.toBeInTheDocument();
    expect(screen.getByText('Record')).toBeVisible();
    rerender(<EntryDetailPageChrome {...props} sidePanel={<p>Files</p>}><p>Record</p></EntryDetailPageChrome>);
    expect(screen.getAllByText('Files')).toHaveLength(1);
  });

  it('retains canvas and fields when a companion panel is supplied', () => {
    render(<EntryDetailPageChrome onClose={() => {}} canvas={<p>Canvas</p>} sidePanel={<p>Files</p>}><p>Fields</p></EntryDetailPageChrome>);
    expect(screen.getByText('Canvas')).toBeVisible();
    expect(screen.getByText('Fields')).toBeVisible();
    expect(screen.getByText('Files')).toBeVisible();
  });
});

describe('attachment revision identification', () => {
  it('shows the added timestamp for retained same-name files', () => {
    const createdAt = '2026-10-08T02:34:19Z';
    render(<AttachmentRow attachment={{ id: 'file-1', filename: 'brief.pdf', mime_type: 'application/pdf', created_at: createdAt }} onOpen={() => {}} onDownload={() => {}} onCopyLink={() => {}} />);
    expect(screen.getByText((text) => text.includes(`Added ${new Date(createdAt).toLocaleString()}`))).toBeVisible();
  });
});
