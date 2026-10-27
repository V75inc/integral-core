import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ContributionLifecycleContext } from '../contributionLifecycle';
import { RelatedRecordControl } from '../RelatedRecordControl';
import { extensionsApi } from '../../../api/extensions';
import { entriesApi } from '../../../api';

vi.mock('../../../api', () => ({ entriesApi: { get: vi.fn() } }));
vi.mock('../../../api/extensions', () => ({
  extensionsApi: { listOperations: vi.fn(), invokeOperation: vi.fn() },
  requireOperationOutput: (value: unknown) => value,
}));

const config = {
  kind: 'contact',
  form_operation: 'contact_form',
  create_operation: 'create_contact',
  prefill: { email: 'recipient' },
};
const form = {
  output: {
    fields: [{ key: 'name', name: 'Name', type: 'text', required: true }],
    defaults: {},
  },
};
function Harness() {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } })
  );
  const [fields, setFields] = useState<Record<string, unknown>>({
    note: 'Keep my draft',
    recipient: 'my-edited@example.invalid',
  });
  return (
    <QueryClientProvider client={client}>
      <ContributionLifecycleContext.Provider
        value={{
          appId: 'app-1',
          mode: 'create',
          placement: 'entry_compose',
          customFields: fields,
          register: () => () => {},
          onDraftPatch: (patch) =>
            setFields((prev) => ({ ...prev, ...patch.custom_fields })),
        }}
      >
        <input
          aria-label="Existing note"
          value={String(fields.note)}
          onChange={(event) =>
            setFields((prev) => ({ ...prev, note: event.target.value }))
          }
        />
        <output data-testid="draft">{JSON.stringify(fields)}</output>
        <RelatedRecordControl
          config={config}
          props={{
            field: {
              key: 'contact',
              name: 'Contact',
              type: 'relation',
              validation: { write_label_to: 'contact_name' },
            },
            value: fields.contact,
            onChange: (id) => setFields((prev) => ({ ...prev, contact: id })),
          }}
          renderField={(props) => (
            <input
              aria-label={props.field.name}
              value={String(props.value || '')}
              onChange={(event) => props.onChange(event.target.value)}
            />
          )}
        />
      </ContributionLifecycleContext.Provider>
    </QueryClientProvider>
  );
}
beforeEach(() => {
  vi.stubGlobal('crypto', {
    randomUUID: () => 'synthetic-intent',
    subtle: { digest: async () => new Uint8Array([1, 2, 3]).buffer },
  });
  vi.mocked(extensionsApi.listOperations).mockResolvedValue({
    operations: [{ key: 'create_contact', allowed: true }],
  });
  vi.mocked(extensionsApi.invokeOperation).mockImplementation(
    async (_app, operation) =>
      (operation === 'contact_form'
        ? form
        : { output: { ok: true, entry_id: 'entry-1' } }) as never
  );
  vi.mocked(entriesApi.get).mockResolvedValue({
    id: 'entry-1',
    title: 'New contact',
    custom_fields: { email: 'source@example.invalid' },
  } as never);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
async function open() {
  render(<Harness />);
  fireEvent.click(await screen.findByRole('button', { name: 'Add contact' }));
  await screen.findByLabelText('Name');
}

describe('related record creation', () => {
  it('preserves the original draft and edited defaults, verifies and selects the saved record', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'New contact' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save and use' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(entriesApi.get).toHaveBeenCalledWith('entry-1');
    expect(screen.getByLabelText('Existing note')).toHaveValue('Keep my draft');
    expect(JSON.parse(screen.getByTestId('draft').textContent!)).toMatchObject({
      contact: 'entry-1',
      contact_name: 'New contact',
      recipient: 'my-edited@example.invalid',
    });
  });

  it('cancels without saving or changing the original draft', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Unsaved contact' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(extensionsApi.invokeOperation).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('Existing note')).toHaveValue('Keep my draft');
  });

  it('asks before replacing existing details with the selected record defaults', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'New contact' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save and use' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByText('Review selected contact details'));
    expect(
      screen.getByText(/Current: my-edited@example.invalid/)
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Use selected details' })
    );
    expect(JSON.parse(screen.getByTestId('draft').textContent!)).toMatchObject({
      recipient: 'source@example.invalid',
      note: 'Keep my draft',
    });
  });

  it('hides creation when the operation is denied', async () => {
    vi.mocked(extensionsApi.listOperations).mockResolvedValue({
      operations: [{ key: 'create_contact', allowed: false }],
    });
    render(<Harness />);
    await waitFor(() =>
      expect(extensionsApi.listOperations).toHaveBeenCalled()
    );
    expect(screen.queryByRole('button', { name: 'Add contact' })).toBeNull();
  });

  it('retains exact command identity and freezes edits after an uncertain write', async () => {
    let attempts = 0;
    vi.mocked(extensionsApi.invokeOperation).mockImplementation(
      async (_app, operation) => {
        if (operation === 'contact_form') return form as never;
        if (++attempts === 1) throw new Error('Network lost');
        return { output: { ok: true, entry_id: 'entry-1' } } as never;
      }
    );
    await open();
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Retry contact' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save and use' }));
    const retry = await screen.findByRole('button', {
      name: 'Check saved result',
    });
    expect(screen.getByLabelText('Name')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.click(retry);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const writes = vi
      .mocked(extensionsApi.invokeOperation)
      .mock.calls.filter((call) => call[1] === 'create_contact');
    expect(writes).toHaveLength(2);
    expect(writes[1]).toEqual(writes[0]);
  });

  it('requires explicit duplicate review before creating a separate identity', async () => {
    vi.mocked(extensionsApi.invokeOperation).mockImplementation(
      async (_app, operation) =>
        operation === 'contact_form'
          ? (form as never)
          : ({
              output: {
                ok: false,
                message: 'Similar contact',
                candidates: [{ id: 'existing', title: 'Existing contact' }],
              },
            } as never)
    );
    await open();
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Similar contact' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save and use' }));
    await screen.findByRole('button', { name: 'Existing contact' });
    expect(screen.getByRole('button', { name: 'Save and use' })).toBeDisabled();
    fireEvent.click(
      screen.getByLabelText('Create a separate record with these details')
    );
    expect(screen.getByRole('button', { name: 'Save and use' })).toBeEnabled();
  });
});
