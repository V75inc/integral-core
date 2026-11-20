import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { publicSharingApi } from '../../api/sharing';
import { SharedTrackPage } from '../SharedTrackPage';
const mocks = vi.hoisted(() => ({ toast: { showToast: vi.fn() }, create: vi.fn().mockResolvedValue({}) }));
vi.mock('../../context/ToastContext', () => ({ useToast: () => mocks.toast }));
vi.mock('../../context/AuthContext', () => ({ useAuthOptional: () => null }));
vi.mock('../../hooks/useMediaQuery', () => ({ useIsMdUp: () => false }));
vi.mock('../../features/memberAssignedForm/memberAssignedFormApi', () => ({ memberAssignedFormApi: {
  load: vi.fn().mockResolvedValue({
    track: { id: 'assigned-track', title: 'Assigned form' },
    public_permissions: { update_entries: true }, views: [], locked: false,
    public_share_extensions: { form_default_group: 'Personal Details', form_readonly_groups: 'Personal Details', form_prefill_notice: 'Details were provided by the issuing organization.' },
    entry: { id: 'assigned-entry', type_id: 'assigned-type', title: 'Prefilled person', custom_fields: { company: 'Prefilled organization', status: 'draft' } },
    entry_types: [{ id: 'assigned-type', name: 'Form', form_schema: {
      base_fields: { body: { enabled: false } },
      fields: [{ key: 'company', name: 'Company', type: 'text', group: 'Personal Details' }],
    } }],
  }),
} }));
vi.mock('../../api/sharing', () => ({ publicSharingApi: {
  getPublicTrack: vi.fn().mockResolvedValue({
    track: { id: 'demo-track', title: 'Synthetic intake', purpose: '' },
    public_permissions: { create_entries: true, read_entries: false }, views: [],
    public_share_extensions: { form_hidden_field_keys: 'status', form_hidden_field_prefixes: 'contract_' },
    entry_types: [{ id: 'demo-type', name: 'Application', form_schema: {
      base_fields: { body: { enabled: false } },
      fields: [{ key: 'company', name: 'Company', type: 'text', required: true }, { key: 'status', name: 'Internal status', type: 'text' }, { key: 'contract_internal', name: 'Internal contract', type: 'text' }],
    } }],
  }), createPublicEntry: mocks.create,
} }));
describe('generic public intake UI', () => {
  it('submits only the bound schema without a type switcher', async () => {
    vi.mocked(publicSharingApi.getPublicTrack).mockResolvedValueOnce({
      track: { id: 'bound-track', title: 'Bound intake' },
      public_entry_type_id: 'bound-type',
      public_permissions: { create_entries: true, read_entries: false }, views: [],
      entry_types: [
        { id: 'other-type', name: 'Other type', form_schema: { fields: [] } },
        { id: 'bound-type', name: 'Application', form_schema: { base_fields: { body: { enabled: false } }, fields: [] } },
      ],
    } as any);
    render(<MemoryRouter initialEntries={['/public/tracks/bound-token']}><Routes>
      <Route path="/public/tracks/:token" element={<SharedTrackPage />} />
    </Routes></MemoryRouter>);
    await screen.findByText('Bound intake');
    expect(screen.queryByRole('combobox')).toBeNull();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Bound application' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith('bound-token', {
      title: 'Bound application', type_id: 'bound-type', body: '', custom_fields: {},
    }));
  });

  it('preserves assigned prefilled personal details as read-only', async () => {
    render(<MemoryRouter initialEntries={['/me/assigned-form']}><Routes>
      <Route path="/me/assigned-form" element={<SharedTrackPage memberFormMode />} />
    </Routes></MemoryRouter>);
    await screen.findByText('Assigned form');
    expect(screen.getByText('Details were provided by the issuing organization.')).toBeTruthy();
    expect(screen.getByText('Prefilled person')).toBeTruthy();
    expect(screen.queryAllByRole('textbox').every(input => input.hasAttribute('disabled') || input.hasAttribute('readonly'))).toBe(true);
  });
  it('renders editable fields and submits synthetic applicant values', async () => {
    render(<MemoryRouter initialEntries={['/public/tracks/demo-token']}><Routes>
      <Route path="/public/tracks/:token" element={<SharedTrackPage />} />
    </Routes></MemoryRouter>);
    await screen.findByText('Synthetic intake');
    expect(screen.queryByText(/filled in from HR records/)).toBeNull();
    expect(screen.queryByText('Internal status')).toBeNull();
    expect(screen.queryByText('Internal contract')).toBeNull();
    const inputs = screen.getAllByRole('textbox');
    expect(inputs).toHaveLength(2);
    inputs.forEach(input => expect(input.hasAttribute('disabled')).toBe(false));
    fireEvent.change(inputs[0], { target: { value: 'DEMO ONLY Application' } });
    fireEvent.change(inputs[1], { target: { value: 'Fictional Company' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith('demo-token', {
      title: 'DEMO ONLY Application', type_id: 'demo-type', body: '', custom_fields: { company: 'Fictional Company' },
    }));
    await screen.findByText('Response Recorded');
    expect(screen.getByRole('button', { name: 'Submit another response' })).toBeTruthy();
  });
  it('uses form-authored plain text and hides the optional repeat CTA', async () => {
    vi.mocked(publicSharingApi.getPublicTrack).mockResolvedValueOnce({
      track: { id: 'custom-track', title: 'Custom intake' },
      public_permissions: { create_entries: true, read_entries: false }, views: [],
      entry_types: [{ id: 'custom-type', name: 'Application', form_schema: {
        base_fields: { body: { enabled: false } }, fields: [],
        confirmation: { title: 'Application received', message: '<b>Review follows</b>', allow_another_response: false },
      } }],
    } as any);
    render(<MemoryRouter initialEntries={['/public/tracks/custom-token']}><Routes>
      <Route path="/public/tracks/:token" element={<SharedTrackPage />} />
    </Routes></MemoryRouter>);
    await screen.findByText('Custom intake');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Fictional application' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await screen.findByRole('heading', { name: 'Application received' });
    expect(screen.getByText('<b>Review follows</b>').querySelector('b')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Submit another response' })).toBeNull();
  });

});
