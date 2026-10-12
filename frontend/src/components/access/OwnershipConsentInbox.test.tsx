import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ inbox: vi.fn(), respond: vi.fn() }));
vi.mock('../../api/accessControl', () => ({ ownershipConsentApi: mocks }));
import { OwnershipConsentInbox } from './OwnershipConsentInbox';
const proposal = { proposal_id: 'proposal', resource_type: 'app', resource_id: 'app-a', status: 'pending', revision: 2, expires_at: '2026-10-30T00:00:00Z', expired: false, ownership_current: true };
describe('Recipient ownership consent', () => {
  afterEach(cleanup);
  beforeEach(() => { vi.clearAllMocks(); mocks.inbox.mockResolvedValue({ complete: true, proposals: [proposal] }); mocks.respond.mockResolvedValue({ status: 'accepted' }); });
  it('submits exact current proposal revision only after deliberate acceptance', async () => {
    render(<OwnershipConsentInbox />);
    expect(mocks.respond).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Review ownership proposals' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Accept ownership' }));
    await waitFor(() => expect(mocks.respond).toHaveBeenCalledWith(proposal, 'accept'));
  });
  it('withholds acceptance for an expired or replaced ownership lifetime', async () => {
    mocks.inbox.mockResolvedValue({ complete: true, proposals: [{ ...proposal, ownership_current: false }] });
    render(<OwnershipConsentInbox />);
    fireEvent.click(screen.getByRole('button', { name: 'Review ownership proposals' }));
    await screen.findByText(/owner changed/);
    expect(screen.queryByRole('button', { name: 'Accept ownership' })).not.toBeInTheDocument();
    expect(mocks.respond).not.toHaveBeenCalled();
  });
});
