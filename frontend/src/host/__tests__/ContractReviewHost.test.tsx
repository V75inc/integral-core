import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ContractReviewHost, ContractReviewRejectedTerminal } from '../ContractReviewHost';

const api = vi.hoisted(() => ({ fetch: vi.fn(), generate: vi.fn(), decide: vi.fn() }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: null }) }));
vi.mock('../../features/memberAssignedForm/memberAssignedFormApi', () => ({
  memberAssignedFormApi: { fetchContractBlob: api.fetch, generateContract: api.generate, decideContract: api.decide },
}));
vi.mock('../../lib/pdfjsLoader', () => ({ assertPdfBlob: async (blob: Blob) => blob }));
vi.mock('../../components/entries/attachments/viewers/BlobPdfViewer', () => ({ BlobPdfViewer: () => <div>Rendered PDF</div> }));

beforeEach(() => {
  vi.resetAllMocks();
  api.fetch.mockResolvedValue(new Blob(['%PDF'], { type: 'application/pdf' }));
});
afterEach(cleanup);

function mount(status = 'generated') {
  const accepted = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ContractReviewHost token="" entryId="form" memberFormMode contractStatus={status} onAccepted={accepted} onRejected={vi.fn()} />
  </QueryClientProvider>);
  return accepted;
}

it('renders the installed review and PDF without accepting automatically', async () => {
  const accepted = mount();
  expect(await screen.findByText('Review and sign')).toBeInTheDocument();
  expect(screen.getByText('Rendered PDF')).toBeInTheDocument();
  expect(accepted).not.toHaveBeenCalled();
  expect(api.generate).not.toHaveBeenCalled();
});

it('generates a missing member document through the owning host API', async () => {
  api.fetch.mockRejectedValueOnce({ response: { status: 404 } });
  mount();
  await waitFor(() => expect(api.generate).toHaveBeenCalledWith({ force: false, entryId: 'form' }));
  expect(await screen.findByText('Review and sign')).toBeInTheDocument();
});

it('shows accepted readback without another signing action', async () => {
  mount('accepted');
  expect(await screen.findByText(/You accepted this document/)).toBeInTheDocument();
  expect(await screen.findByText('Rendered PDF')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument();
  expect(api.decide).not.toHaveBeenCalled();
});

it('exports the rejected terminal required by the pinned Core page', () => {
  render(<ContractReviewRejectedTerminal />);
  expect(screen.getByRole('heading', { name: 'Form closed' })).toBeInTheDocument();
});
