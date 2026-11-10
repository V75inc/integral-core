import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { aiChatApi } from '../../../../api/aiChat';
import { ApprovalRequestContent, approvalRequestSentence } from '../ApprovalRequestContent';

vi.mock('../../../../components/entries/relations/RelationValue', () => ({
  RelationValue: ({ value }: {value: string}) => <span>{value}</span>,
}));
vi.mock('../../../../api/aiChat', () => ({ aiChatApi: { downloadChatAttachmentForReview: vi.fn() } }));
afterEach(cleanup);

describe('plain-language approval request', () => {
  it('articulates the proposed effect without claiming it already happened', () => {
    expect(approvalRequestSentence('Create entry “Sam Blair” in Customers')).toBe('This will create entry “Sam Blair” in Customers.');
    expect(approvalRequestSentence('File content as “Sam Blair” in Customers')).toBe('This will save “Sam Blair” in Customers.');
    expect(approvalRequestSentence('Update the selected records.')).toBe('This will update the selected records.');
    expect(approvalRequestSentence('Review customer information')).toBe('Review customer information');
    expect(approvalRequestSentence('')).toMatch(/Review the details/);
  });

  it('shows prose first and keeps the complete detailed change available', () => {
    render(<ApprovalRequestContent staged={{kind:'create_entry', summary:'Create entry “Sam Blair”',
      diff_human:'Create entry “Sam Blair”\n\n- **Body:** Exact proposed content',
      diff_machine: {op:'create_entry', body:'Exact proposed content'},
    }} />);
    expect(screen.getByText('This will create entry “Sam Blair”.')).toBeVisible();
    expect(screen.getByText('Review change details').closest('details')).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('Review change details'));
    expect(screen.getByText('Exact proposed content')).toBeVisible();
  });

  it('keeps bound command inputs visible and never substitutes narrated JSON', () => {
    render(<ApprovalRequestContent staged={{kind:'call_workspace_tool', summary:'Save selected material',
      diff_human:'```json\n{"amount":999999}\n```',
      diff_machine: {op:'call_workspace_tool', input:{amount:24}, review:{description:'Record the approved amount.'}},
    }} />);
    expect(screen.getByText('This will save selected material.')).toBeVisible();
    expect(screen.getByText('24')).toBeVisible();
    expect(screen.queryByText(/999999/)).not.toBeInTheDocument();
    expect(screen.getByText('Record the approved amount.')).toBeVisible();
  });

  it('explains the actual scheduled task and stop condition without a truncated headline', () => {
    render(<ApprovalRequestContent staged={{kind:'routine_task_create', summary:'New routine: Read the current total in workspace n.W',
      diff_human:'Instruction: Read the current total.\nCadence: * * * * *',
      diff_machine:{op:'routine_task_create',instruction:'Read the current accessible entry total and report it here.',
        cron:'* * * * *',timezone:'UTC',delivery_mode:'agent',max_runs:5,write_scope:[]},
    }} />);
    expect(screen.getByText('This will schedule an automatic update in this chat.')).toBeVisible();
    expect(screen.getByText('Read the current accessible entry total and report it here.')).toBeVisible();
    expect(screen.getByText(/every minute \(UTC\).*stop after 5 successful runs/)).toBeVisible();
    expect(screen.queryByText(/New routine:/)).not.toBeInTheDocument();
  });

  it('keeps unattended write authority visible before a routine is approved', () => {
    render(<ApprovalRequestContent staged={{kind:'routine_task_create',summary:'New routine: Update records',diff_human:'Track scope',
      diff_machine:{op:'routine_task_create',instruction:'Update approved records.',cron:'0 9 * * *',delivery_mode:'agent',write_scope:[{resource_type:'track',resource_id:'n.Track.1'}]},
    }} />);
    expect(screen.getByText(/Future runs may change the approved resources without another prompt/)).toBeVisible();
    expect(screen.getByText(/continue until you pause or stop/)).toBeVisible();
  });
});

describe('file approval preview', () => {
  it('offers the bound chat file for review inside a batch', async () => {
    const fetchFile = vi.mocked(aiChatApi.downloadChatAttachmentForReview);
    fetchFile.mockResolvedValueOnce(new Blob(['reviewed']));
    const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test');
    const revokeUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const machine = { op: 'attach_uploaded_file', attachment_id: 'n.Attachment.1', expected_sha256: 'a'.repeat(64),
      file_review: { thread_id: 'n.ChatThread.1', attachment_id: 'n.Attachment.1', filename: 'Report.docx' } };
    render(<ApprovalRequestContent staged={{ kind: 'batch', summary: 'File report', diff_human: '',
      diff_machine: { operations: [{ kind: 'attach_uploaded_file', diff_machine: machine }] } }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download Report.docx for review' }));
    await waitFor(() => expect(fetchFile).toHaveBeenCalledWith('n.ChatThread.1', 'n.Attachment.1', 'a'.repeat(64)));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(revokeUrl).toHaveBeenCalledWith('blob:test');
    createUrl.mockRestore(); revokeUrl.mockRestore(); click.mockRestore();
  });

  it('does not offer a review link for a different attachment', () => {
    render(<ApprovalRequestContent staged={{ kind: 'attach_uploaded_file', summary: 'Attach file', diff_human: '',
      diff_machine: { op: 'attach_uploaded_file', attachment_id: 'n.Attachment.1', expected_sha256: 'a'.repeat(64),
        file_review: { thread_id: 'n.ChatThread.1', attachment_id: 'n.Attachment.2', filename: 'Other.pdf' } } }} />);
    expect(screen.queryByRole('button', { name: /for review/ })).not.toBeInTheDocument();
  });

  it('explains when the authorized review download is no longer available', async () => {
    vi.mocked(aiChatApi.downloadChatAttachmentForReview).mockRejectedValueOnce(new Error('denied'));
    render(<ApprovalRequestContent staged={{ kind: 'attach_uploaded_file', summary: 'Attach file', diff_human: '',
      diff_machine: { op: 'attach_uploaded_file', attachment_id: 'n.Attachment.1', expected_sha256: 'a'.repeat(64),
        file_review: { thread_id: 'n.ChatThread.1', attachment_id: 'n.Attachment.1', filename: 'Report.pdf' } } }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download Report.pdf for review' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('may no longer have access');
  });

  it('shows the exact preview as text and labels incomplete content', () => {
    const text = '<img src=x onerror=alert(1)>\nDraft paragraph';
    const { container } = render(<ApprovalRequestContent staged={{
      kind: 'attach_file', summary: 'Attach report', diff_human: '',
      diff_machine: { op: 'attach_file', file_preview: { available: true, text, complete: false } },
    }} />);
    expect(screen.getByText(/Partial preview/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Preview file content'));
    expect(container.querySelector('pre')).toHaveTextContent(text, { normalizeWhitespace: false });
    expect(container.querySelector('pre')).toBeVisible();
    expect(container.querySelector('img')).not.toBeInTheDocument();
  });

  it('says when a preview is unavailable', () => {
    render(<ApprovalRequestContent staged={{
      kind: 'attach_file', summary: 'Attach report', diff_human: '',
      diff_machine: { op: 'attach_file', file_preview: { available: false } },
    }} />);
    expect(screen.getByText(/content preview is not available/)).toBeVisible();
  });
});
