import { useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
import { LiveValuesContext, useLiveValuesProvider } from '../regionConditions';
import { ContributionLifecycleContext } from '../../entries/contributionLifecycle';
import { useDetailRecordEdit } from '../../entries/useDetailRecordEdit';
import type { Entry, SavedView } from '../../../types';

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

  it('detail mode: fields are read-only (view until pencil edit)', async () => {
    mockEntryTypesList.mockResolvedValue([entryType]);
    mockGet.mockResolvedValue({
      id: 'entry-1',
      title: 'Schedule',
      custom_fields: { employer_name: 'Acme Co' },
    });

    const { ContributionLifecycleContext } = await import(
      '../../entries/contributionLifecycle'
    );

    render(
      <ContributionLifecycleContext.Provider
        value={{
          mode: 'detail',
          placement: 'entry_detail',
          appId: 'app-1',
          entryId: 'entry-1',
          customFields: { employer_name: 'Acme Co' },
          register: () => () => {},
        }}
      >
        <FormRegionWidget
          view={baseView({
            fields: ['employer_name'],
            title: 'Header',
            __bindings: { entryId: 'entry-1' },
          })}
          entries={[]}
          isLoading={false}
          onEntryOpen={() => {}}
        />
      </ContributionLifecycleContext.Provider>
    );

    const input = await screen.findByDisplayValue('Acme Co');
    expect(input).toBeDisabled();
    fireEvent.change(input, { target: { value: 'Edited' } });
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it('detail mode: editable_in_detail fields stay writable', async () => {
    const invoiceType = {
      id: 'et-inv',
      name: 'invoice',
      form_schema: {
        _manifest_entry_type_key: 'invoice',
        fields: [
          { key: 'invoice_number', name: 'Invoice no.', type: 'text' },
          {
            key: 'status',
            name: 'Status',
            type: 'select',
            enum: ['draft', 'sent', 'paid'],
          },
        ],
      },
    };
    mockEntryTypesList.mockResolvedValue([invoiceType]);
    mockGet.mockResolvedValue({
      id: 'inv-1',
      title: 'INV-0001',
      custom_fields: { invoice_number: 'INV-0001', status: 'draft' },
    });
    mockUpdate.mockResolvedValue({
      id: 'inv-1',
      title: 'INV-0001',
      custom_fields: { invoice_number: 'INV-0001', status: 'sent' },
    });

    const { ContributionLifecycleContext } = await import(
      '../../entries/contributionLifecycle'
    );

    render(
      <ContributionLifecycleContext.Provider
        value={{
          mode: 'detail',
          placement: 'entry_detail',
          appId: 'app-1',
          entryId: 'inv-1',
          customFields: { invoice_number: 'INV-0001', status: 'draft' },
          register: () => () => {},
        }}
      >
        <FormRegionWidget
          view={{
            ...baseView({
              fields: [
                'invoice_number',
                { key: 'status', editable_in_detail: true },
              ],
              __bindings: { entryId: 'inv-1' },
            }),
            default_entry_type_key: 'invoice',
          }}
          entries={[]}
          isLoading={false}
          onEntryOpen={() => {}}
        />
      </ContributionLifecycleContext.Provider>
    );

    const numberInput = await screen.findByDisplayValue('INV-0001');
    expect(numberInput).toBeDisabled();

    const statusTrigger = await screen.findByRole('combobox', { name: /Status/i });
    expect(statusTrigger).not.toBeDisabled();
    fireEvent.click(statusTrigger);
    const sentOption = await screen.findByRole('option', { name: /^Sent$/i });
    fireEvent.click(sentOption);
    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith('inv-1', {
        custom_fields: { status: 'sent' },
      });
    });
  });

  it('detail mode: an editor picks a date and it saves only on Save', async () => {
    mockEntryTypesList.mockResolvedValue([{
      ...entryType,
      form_schema: {
        ...entryType.form_schema,
        fields: [
          { key: 'start_date', name: 'Start date', type: 'date' },
          { key: 'locked_date', name: 'Locked date', type: 'date', readonly: true },
        ],
      },
    }]);
    const record = {
      id: 'entry-1', title: 'Schedule', record_revision: 3, schema_revision: 2,
      custom_fields: { start_date: '2026-03-10', locked_date: '2026-03-11' },
    };
    const saved = {
      ...record, record_revision: 4,
      custom_fields: { ...record.custom_fields, start_date: '2026-03-20' },
    };
    mockGet.mockResolvedValue(record);
    mockUpdate.mockResolvedValue(saved);
    const onSaved = vi.fn();
    const { ContributionLifecycleContext } = await import(
      '../../entries/contributionLifecycle'
    );

    render(
      <>
        <ContributionLifecycleContext.Provider
          value={{
            mode: 'detail', placement: 'entry_detail', entryId: 'entry-1',
            customFields: record.custom_fields,
            detailEdit: { readOnlyKeys: [], onSaved },
            register: () => () => {},
          }}
        >
          <FormRegionWidget
            view={baseView({
              fields: ['start_date', 'locked_date'],
              __bindings: { entryId: 'entry-1' },
            })}
            entries={[]}
            isLoading={false}
            onEntryOpen={() => {}}
          />
        </ContributionLifecycleContext.Provider>
      </>
    );

    const [open, locked] = await screen.findAllByRole('button', { name: 'Open calendar' });
    expect(open).not.toBeDisabled();
    expect(locked).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();

    mockGet.mockResolvedValue(saved);
    fireEvent.click(open);
    fireEvent.click(await screen.findByRole('button', { name: /March 20th, 2026/ }));
    expect(mockUpdate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, { current: true }));
    expect(mockUpdate).toHaveBeenCalledWith('entry-1', {
      custom_fields: { start_date: '2026-03-20' },
      expected_record_revision: 3,
      expected_schema_revision: 2,
    });
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('draft bind: reads lifecycle.customFields and patches via onDraftPatch (no entriesApi)', async () => {
    mockEntryTypesList.mockResolvedValue([entryType]);
    const onDraftPatch = vi.fn();

    const { ContributionLifecycleContext } = await import(
      '../../entries/contributionLifecycle'
    );

    render(
      <ContributionLifecycleContext.Provider
        value={{
          mode: 'create',
          placement: 'entry_compose',
          customFields: { employer_name: 'Draft Co', registration_number: '' },
          onDraftPatch,
          register: () => () => {},
        }}
      >
        <FormRegionWidget
          view={baseView({
            fields: ['employer_name'],
            title: 'Header',
          })}
          entries={[]}
          isLoading={false}
          onEntryOpen={() => {}}
        />
      </ContributionLifecycleContext.Provider>
    );

    const input = await screen.findByDisplayValue('Draft Co');
    expect(mockGet).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: 'Patched Co' } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(onDraftPatch).toHaveBeenCalledWith({
        custom_fields: { employer_name: 'Patched Co' },
      });
    });
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it('draft bind: leaves hide_on_create fields out of the create form', async () => {
    mockEntryTypesList.mockResolvedValue([
      {
        ...entryType,
        form_schema: {
          ...entryType.form_schema,
          fields: [
            entryType.form_schema.fields[0],
            { ...entryType.form_schema.fields[1], hide_on_create: true },
          ],
        },
      },
    ]);
    const { ContributionLifecycleContext } = await import(
      '../../entries/contributionLifecycle'
    );

    render(
      <ContributionLifecycleContext.Provider
        value={{
          mode: 'create',
          placement: 'entry_compose',
          customFields: { employer_name: 'Draft Co', registration_number: 'R-1' },
          onDraftPatch: vi.fn(),
          register: () => () => {},
        }}
      >
        <FormRegionWidget
          view={baseView({ fields: ['employer_name', 'registration_number'] })}
          entries={[]}
          isLoading={false}
          onEntryOpen={() => {}}
        />
      </ContributionLifecycleContext.Provider>
    );

    await screen.findByDisplayValue('Draft Co');
    expect(screen.queryByDisplayValue('R-1')).not.toBeInTheDocument();
  });
});

describe('FormRegionWidget saving dates on the reading view', () => {
  const dateType = {
    ...entryType,
    form_schema: {
      ...entryType.form_schema,
      fields: [
        { key: 'start_date', name: 'Start date', type: 'date' },
        { key: 'end_date', name: 'End date', type: 'date' },
      ],
    },
  };
  const record = (id: string, start: string, end: string, revision = 3): Entry => ({
    id, track_id: 'track-1', title: id, record_revision: revision, schema_revision: 2,
    custom_fields: { start_date: start, end_date: end },
  }) as unknown as Entry;

  /** The entry dialog's own wiring: one widget, whichever record is shown. */
  function Host({ entry, canSwitchTo, newer, onUpdate = () => {} }: {
    entry: Entry; canSwitchTo?: Entry; newer?: Entry; onUpdate?: (saved: Entry) => void;
  }) {
    const [shown, setShown] = useState(entry);
    const detailEdit = useDetailRecordEdit(shown, true, setShown, onUpdate);
    // Seeded with the whole record, as the layout container does.
    const live = useLiveValuesProvider(shown.custom_fields as Record<string, unknown>);
    return (
      <LiveValuesContext.Provider value={live}>
        <span data-testid="shown">{shown.id}</span>
        {canSwitchTo && <button type="button" onClick={() => setShown(canSwitchTo)}>Open next</button>}
        {canSwitchTo && <button type="button" onClick={() => setShown(entry)}>Open first</button>}
        {newer && <button type="button" onClick={() => setShown(newer)}>Host refreshed</button>}
        <ContributionLifecycleContext.Provider
          value={{
            mode: 'detail', placement: 'entry_detail', entryId: shown.id,
            customFields: shown.custom_fields as Record<string, unknown>,
            detailEdit, register: () => () => {},
          }}
        >
          <FormRegionWidget
            view={baseView({ fields: ['start_date', 'end_date'], __bindings: { entryId: shown.id } })}
            entries={[]}
            isLoading={false}
            onEntryOpen={() => {}}
          />
        </ContributionLifecycleContext.Provider>
      </LiveValuesContext.Provider>
    );
  }

  const mount = (host: ReactNode) => render(
    <QueryClientProvider client={new QueryClient()}>{host}</QueryClientProvider>,
  );

  async function pickStart(day: RegExp) {
    const [start] = await screen.findAllByRole('button', { name: 'Open calendar' });
    fireEvent.click(start);
    fireEvent.click(await screen.findByRole('button', { name: day }));
  }

  /** The picker shows "10th April, 2026", or 10/04/2026 for a date just picked. */
  const shownDate = (day: number, month = 3) => new RegExp(
    `^(${day}(st|nd|rd|th) ${['March', 'April'][month - 3]}, 2026|${day}/0${month}/2026)$`,
  );

  it('shows and saves against the same revision when the shared snapshot is older', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const older = record('entry-a', '2026-03-10', '2026-03-11', 3);
    const current = record('entry-a', '2026-04-10', '2026-04-11', 4);
    mockGet.mockResolvedValue(current);
    mockUpdate.mockResolvedValue(record('entry-a', '2026-04-22', '2026-04-11', 5));
    mount(<Host entry={older} />);

    await waitFor(() => expect(screen.getByDisplayValue(shownDate(10, 4))).toBeInTheDocument());
    expect(screen.queryByDisplayValue(shownDate(10))).toBeNull();
    await pickStart(/April 22nd, 2026/);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('entry-a', {
      custom_fields: { start_date: '2026-04-22' },
      expected_record_revision: 4,
      expected_schema_revision: 2,
    }));
  });

  it('derives a date offset from the save revision when the schema arrives after the record', async () => {
    const offsetType = {
      ...dateType,
      form_schema: {
        ...dateType.form_schema,
        fields: [
          dateType.form_schema.fields[0],
          {
            ...dateType.form_schema.fields[1],
            validation: { auto_offset: { from_date: 'start_date', from_terms: 'terms' } },
          },
          {
            key: 'terms', name: 'Terms', type: 'select',
            validation: { term_days: { seven_days: 7, fourteen_days: 14 } },
          },
        ],
      },
    };
    const older = record('entry-a', '2026-03-10', '2026-03-17', 3);
    const current = record('entry-a', '2026-04-10', '2026-04-24', 4);
    older.custom_fields = { ...older.custom_fields, terms: 'seven_days' };
    current.custom_fields = { ...current.custom_fields, terms: 'fourteen_days' };
    let loadSchema!: (types: (typeof offsetType)[]) => void;
    mockEntryTypesList.mockReturnValue(new Promise(resolve => { loadSchema = resolve; }));
    mockGet.mockResolvedValue(current);
    mockUpdate.mockResolvedValue({
      ...current,
      record_revision: 5,
      custom_fields: { ...current.custom_fields, start_date: '2026-04-22', end_date: '2026-05-06' },
    });
    await act(async () => { mount(<Host entry={older} />); });
    expect(mockGet).toHaveBeenCalledWith('entry-a');
    await act(async () => { loadSchema([offsetType]); });

    await pickStart(/April 22nd, 2026/);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('entry-a', {
      custom_fields: { start_date: '2026-04-22', end_date: '2026-05-06' },
      expected_record_revision: 4,
      expected_schema_revision: 2,
    }));
  });

  it('masks a projection that becomes unavailable while a date draft is open', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const first: Entry = {
      ...record('entry-a', '2026-03-10', '2026-03-11', 3),
      read_time_fields: { values: { end_date: '2026-03-29' }, status: { end_date: 'available' } },
    };
    const newer: Entry = {
      ...record('entry-a', '2026-03-10', '2026-03-11', 4),
      read_time_fields: { values: { end_date: null }, status: { end_date: 'unavailable' } },
    };
    mockGet.mockResolvedValue(first);
    mount(<Host entry={first} newer={newer} />);

    await screen.findByDisplayValue(shownDate(29));
    await pickStart(/March 20th, 2026/);
    mockGet.mockResolvedValue(newer);
    fireEvent.click(screen.getByRole('button', { name: 'Host refreshed' }));

    await screen.findByText(/could not be checked/);
    expect(screen.getByDisplayValue(shownDate(20))).toBeInTheDocument();
    const endDate = screen.getByRole('textbox', { name: 'End date' });
    expect(endDate).toBeDisabled();
    expect(endDate).toHaveValue('');
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  /** Starts a save of entry A that finishes only when the test says so. */
  async function saveHeld(a: Entry) {
    let finish: (entry: Entry) => void = () => {};
    mockUpdate.mockImplementation(() => new Promise<Entry>(resolve => { finish = resolve; }));
    await pickStart(/March 20th, 2026/);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    return async (saved: Entry) => {
      // Reads fail while the save finishes, so only the save's own response can
      // change the screen, however late a re-read of the region happens to run.
      mockGet.mockRejectedValue(new Error('offline'));
      await act(async () => { finish(saved); });
      mockGet.mockResolvedValue(a);
    };
  }

  it('ignores a save older than the record now shown', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const first = record('entry-a', '2026-03-10', '2026-03-11', 3);
    const newer = record('entry-a', '2026-03-25', '2026-03-28', 5);
    mockGet.mockResolvedValue(first);
    const onUpdate = vi.fn();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    render(<QueryClientProvider client={client}>
      <Host entry={first} newer={newer} onUpdate={onUpdate} />
    </QueryClientProvider>);

    const finish = await saveHeld(newer);
    mockGet.mockResolvedValue(newer);
    fireEvent.click(screen.getByRole('button', { name: 'Host refreshed' }));
    await act(async () => {});
    await finish(record('entry-a', '2026-03-20', '2026-03-11', 4));

    expect(onUpdate).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue(shownDate(25))).toBeInTheDocument();
    expect(screen.getByDisplayValue(shownDate(28))).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['entry', 'entry-a'] });
  });

  it('ignores a save from before the person left and returned to the record', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const a = record('entry-a', '2026-03-10', '2026-03-11', 3);
    const b = record('entry-b', '2026-04-10', '2026-04-11', 3);
    mockGet.mockImplementation(async (id: string) => (id === 'entry-b' ? b : a));
    const onUpdate = vi.fn();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    render(<QueryClientProvider client={client}>
      <Host entry={a} canSwitchTo={b} onUpdate={onUpdate} />
    </QueryClientProvider>);

    const finish = await saveHeld(a);
    fireEvent.click(screen.getByRole('button', { name: 'Open next' }));
    await waitFor(() => expect(screen.getByDisplayValue(shownDate(10, 4))).toBeInTheDocument());
    mockGet.mockImplementation(async () => a);
    fireEvent.click(screen.getByRole('button', { name: 'Open first' }));
    // Back on A with its save still under way.
    await waitFor(() => expect(screen.getByDisplayValue(shownDate(11))).toBeInTheDocument());
    await finish(record('entry-a', '2026-03-20', '2026-03-11', 4));

    expect(onUpdate).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue(shownDate(10))).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['entry', 'entry-a'] });
  });

  it('a save finishing late leaves the next record and its draft alone', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const a = record('entry-a', '2026-03-10', '2026-03-11');
    const b = record('entry-b', '2026-04-10', '2026-04-11');
    const savedA = record('entry-a', '2026-03-20', '2026-03-11', 4);
    let aSaved = false;
    mockGet.mockImplementation(async (id: string) => (id === 'entry-b' ? b : aSaved ? savedA : a));
    let finish: (entry: Entry) => void = () => {};
    mockUpdate.mockImplementation(() => new Promise<Entry>(resolve => { finish = resolve; }));
    const onUpdate = vi.fn();
    mount(<Host entry={a} canSwitchTo={b} onUpdate={onUpdate} />);

    await pickStart(/March 20th, 2026/);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open next' }));
    await pickStart(/April 22nd, 2026/);
    aSaved = true;
    await act(async () => { finish(savedA); });

    expect(screen.getByTestId('shown')).toHaveTextContent('entry-b');
    // The dialog's owner is not handed the earlier record back either.
    expect(onUpdate).not.toHaveBeenCalled();
    mockUpdate.mockResolvedValue(record('entry-b', '2026-04-22', '2026-04-11', 4));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenLastCalledWith('entry-b', {
      custom_fields: { start_date: '2026-04-22' },
      expected_record_revision: 3,
      expected_schema_revision: 2,
    }));
  });

  it('shows a newer end date after the start date was saved', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const first = record('entry-a', '2026-03-10', '2026-03-11');
    const saved = record('entry-a', '2026-03-20', '2026-03-11', 4);
    const newer = record('entry-a', '2026-03-25', '2026-03-28', 5);
    mockGet.mockResolvedValue(first);
    mockUpdate.mockResolvedValue(saved);
    mount(<Host entry={first} newer={newer} />);

    await pickStart(/March 20th, 2026/);
    mockGet.mockResolvedValue(saved);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Save' })).toBeNull());
    mockGet.mockResolvedValue(newer);
    fireEvent.click(screen.getByRole('button', { name: 'Host refreshed' }));

    await waitFor(() => expect(screen.getByDisplayValue(/28/)).toBeInTheDocument());
    expect(screen.getByDisplayValue(/25/)).toBeInTheDocument();
  });

  it('shows a newer revision even while the shared snapshot is still the old one', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const first = record('entry-a', '2026-03-10', '2026-03-11');
    const saved = record('entry-a', '2026-03-20', '2026-03-11', 4);
    const newer = record('entry-a', '2026-03-25', '2026-03-28', 5);
    mockGet.mockResolvedValue(first);
    mockUpdate.mockResolvedValue(saved);
    /** A container whose own snapshot never changes; only the record is re-read. */
    function StaleSnapshot() {
      const [, setReads] = useState(0);
      const live = useLiveValuesProvider(first.custom_fields as Record<string, unknown>);
      return (
        <LiveValuesContext.Provider value={live}>
          <button type="button" onClick={() => setReads(n => n + 1)}>Read again</button>
          <ContributionLifecycleContext.Provider
            value={{
              mode: 'detail', placement: 'entry_detail', entryId: 'entry-a', customFields: {},
              detailEdit: { readOnlyKeys: [], onSaved: () => {} }, register: () => () => {},
            }}
          >
            <FormRegionWidget
              view={baseView({ fields: ['start_date', 'end_date'], __bindings: { entryId: 'entry-a' } })}
              entries={[]}
              isLoading={false}
              onEntryOpen={() => {}}
            />
          </ContributionLifecycleContext.Provider>
        </LiveValuesContext.Provider>
      );
    }
    mount(<StaleSnapshot />);

    await pickStart(/March 20th, 2026/);
    mockGet.mockResolvedValue(newer);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Save' })).toBeNull());

    await waitFor(() => expect(screen.getByDisplayValue(/28/)).toBeInTheDocument());
    expect(screen.getByDisplayValue(/25/)).toBeInTheDocument();
  });

  it('reads again, never writes again, when the refresh after a save fails', async () => {
    mockEntryTypesList.mockResolvedValue([dateType]);
    const first = record('entry-a', '2026-03-10', '2026-03-11');
    const saved = record('entry-a', '2026-03-20', '2026-03-11', 4);
    // The end date is worked out at read time, so the PATCH response lacks it.
    const projected = { ...first, read_time_fields: {
      values: { end_date: '2026-03-11' }, status: {},
    } } as Entry;
    mockGet.mockResolvedValue(projected);
    mockUpdate.mockResolvedValue(saved);
    mount(<Host entry={projected} />);

    await pickStart(/March 20th, 2026/);
    mockGet.mockRejectedValue(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const retry = await screen.findByRole('button', { name: 'Refresh entry' });
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Open calendar' })[1]).toBeDisabled();
    // The saved date stays on screen; the live one is unknown, not the stored one.
    expect(screen.getByDisplayValue(shownDate(20))).toBeInTheDocument();
    expect(screen.queryByDisplayValue(shownDate(11))).toBeNull();
    expect(screen.getByText(/could not be checked/)).toBeInTheDocument();

    mockGet.mockResolvedValue({ ...saved, read_time_fields: {
      values: { end_date: '2026-03-29' }, status: {},
    } });
    fireEvent.click(retry);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Refresh entry' })).toBeNull());
    await waitFor(() => expect(screen.getByDisplayValue(shownDate(29))).toBeInTheDocument());
    expect(screen.queryByText(/could not be checked/)).toBeNull();
    expect(mockUpdate).toHaveBeenCalledTimes(1);
  });
});

it('resolves the requested host type by display name when another type comes first', async () => {
  mockEntryTypesList.mockResolvedValue([
    {id: 'post', name: 'Post', form_schema: {fields: [{key: 'body', name: 'Body', type: 'text'}]}},
    {id: 'document', name: 'Document', form_schema: {_manifest_entry_type_key: 'document', fields: [{key: 'code', name: 'Document code', type: 'text'}]}},
  ]);
  mockGet.mockResolvedValue({id: 'entry-1', title: 'Document', custom_fields: {code: 'DOC-7'}});
  const view = {...baseView({fields: ['code'], show_labels: true, __bindings: {entryId: 'entry-1'}}), default_entry_type_key: 'Document'};
  render(<FormRegionWidget view={view} entries={[]} isLoading={false} onEntryOpen={() => {}} />);
  await screen.findByDisplayValue('DOC-7');
  expect(screen.getByText('Document code')).toBeInTheDocument();
});
