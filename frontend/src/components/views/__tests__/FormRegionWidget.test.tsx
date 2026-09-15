import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

const mockGet = vi.fn();
const mockUpdate = vi.fn();
const mockEntryTypesList = vi.fn();
const mockTracksList = vi.fn();
const mockToolsCall = vi.fn();
const mockShowToast = vi.fn();

vi.mock('../../../api', async importOriginal => {
  const actual = await importOriginal<typeof import('../../../api')>();
  return {
    ...actual,
    entriesApi: {
      ...actual.entriesApi,
      get: (id: string) => mockGet(id),
      update: (id: string, data: Record<string, unknown>) => mockUpdate(id, data),
      list: async () => [],
    },
    entryTypesApi: { ...actual.entryTypesApi, list: (p?: unknown) => mockEntryTypesList(p) },
    tracksApi: { ...actual.tracksApi, list: () => mockTracksList() },
  };
});

vi.mock('../../../api/tools', () => ({
  toolsApi: { call: (key: string, input: Record<string, unknown>) => mockToolsCall(key, input) },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}));

import { FormRegionWidget } from '../FormRegionWidget';
import type { SavedView } from '../../../types';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const entryType = {
  id: 'et-1',
  name: 'schedule',
  form_schema: {
    _manifest_entry_type_key: 'nis_schedule',
    fields: [
      { key: 'employer_name', name: 'Employer Name', type: 'text' },
      { key: 'registration_number', name: 'Registration Number', type: 'text' },
    ],
  },
};

function baseView(config: Record<string, unknown>): SavedView {
  return {
    id: 'view-1',
    name: 'Schedule Header',
    type: 'form_region',
    track_id: 'track-1',
    default_entry_type_key: 'nis_schedule',
    config,
  };
}

describe('FormRegionWidget', () => {
  it('self bind mode: fetches the host entry and renders configured fields', async () => {
    mockEntryTypesList.mockResolvedValue([entryType]);
    mockGet.mockResolvedValue({
      id: 'entry-1',
      title: 'Schedule',
      custom_fields: { employer_name: 'Acme Co', registration_number: 'REG-1' },
    });

    render(
      <FormRegionWidget
        view={baseView({
          fields: ['employer_name', 'registration_number'],
          title: 'Header',
          __bindings: { entryId: 'entry-1' },
        })}
        entries={[]}
        isLoading={false}
        onEntryOpen={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Header')).toBeInTheDocument();
    });
    expect(mockGet).toHaveBeenCalledWith('entry-1');
    expect(screen.getByDisplayValue('Acme Co')).toBeInTheDocument();
    expect(screen.getByDisplayValue('REG-1')).toBeInTheDocument();
  });

  it('self bind mode: persists a field edit via entriesApi.update', async () => {
    mockEntryTypesList.mockResolvedValue([entryType]);
    mockGet.mockResolvedValue({
      id: 'entry-1',
      title: 'Schedule',
      custom_fields: { employer_name: 'Acme Co', registration_number: 'REG-1' },
    });
    mockUpdate.mockResolvedValue({
      id: 'entry-1',
      title: 'Schedule',
      custom_fields: { employer_name: 'New Name', registration_number: 'REG-1' },
    });

    render(
      <FormRegionWidget
        view={baseView({
          fields: ['employer_name', 'registration_number'],
          __bindings: { entryId: 'entry-1' },
        })}
        entries={[]}
        isLoading={false}
        onEntryOpen={() => {}}
      />
    );

    const input = await screen.findByDisplayValue('Acme Co');
    fireEvent.change(input, { target: { value: 'New Name' } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith('entry-1', {
        custom_fields: { employer_name: 'New Name' },
      });
    });
  });

  it('tool bind mode: resolves a related entry via a workspace tool and renders it editable', async () => {
    mockEntryTypesList.mockResolvedValue([entryType]);
    mockToolsCall.mockResolvedValue({ output: { entry_id: 'comp-1' } });
    mockGet.mockResolvedValue({
      id: 'comp-1',
      title: 'Compensation',
      custom_fields: { employer_name: 'From Tool', registration_number: '' },
    });

    render(
      <FormRegionWidget
        view={baseView({
          fields: ['employer_name'],
          __bindings: { entryId: 'entry-1', entry: 'tool', tool: 'get_compensation' },
        })}
        entries={[]}
        isLoading={false}
        onEntryOpen={() => {}}
      />
    );

    await waitFor(() => {
      expect(mockToolsCall).toHaveBeenCalledWith('get_compensation', { entry_id: 'entry-1' });
    });
    expect(screen.getByDisplayValue('From Tool')).toBeInTheDocument();
  });

  it('anchored bind mode: reads the first entry already delivered via props', async () => {
    mockEntryTypesList.mockResolvedValue([entryType]);

    render(
      <FormRegionWidget
        view={baseView({
          fields: ['employer_name'],
          __bindings: { entry: 'anchored' },
        })}
        entries={[
          {
            id: 'anchor-1',
            title: 'Anchored entry',
            custom_fields: { employer_name: 'Anchored Value' },
          } as never,
        ]}
        isLoading={false}
        onEntryOpen={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByDisplayValue('Anchored Value')).toBeInTheDocument();
    });
    expect(mockGet).not.toHaveBeenCalled();
  });
});
