import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import type { ContentProfileFieldSpec, Entry, SavedView } from '../../../types';

import { TableWidget } from '../TableWidget';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function view(config: Record<string, unknown>): SavedView {
  return {
    id: 'v-1',
    name: 'Employees',
    type: 'table',
    track_id: 'employees-track',
    config,
  } as SavedView;
}

const noop = () => {};

describe('TableWidget select-field rendering', () => {
  it("humanizes a select field's raw stored value, matching every other select control in the app", () => {
    // payroll-app's employee `status` is exactly this shape: a select field
    // (enum: active/inactive/…) stored lowercase — the create form's own
    // select control shows "Active" (SeamlessField's resolveEnumOptionLabel/
    // humanizeEnumValue), but the table cell used to print the raw stored
    // value verbatim since the default cell renderer had no way to know
    // `custom_fields.status` was select-typed at all.
    const entries: Entry[] = [
      {
        id: 'e1',
        title: 'Ava Persaud',
        custom_fields: { status: 'active' },
      } as unknown as Entry,
    ];
    const fields: ContentProfileFieldSpec[] = [
      { key: 'status', name: 'Status', type: 'select', enum: ['active', 'inactive'] },
    ];

    render(
      <TableWidget
        view={view({
          columns: [{ field: 'custom_fields.status', label: 'Status' }],
        })}
        entries={entries}
        fields={fields}
        isLoading={false}
        onEntryOpen={noop}
      />
    );

    expect(screen.getAllByText('Active').length).toBeGreaterThan(0);
    expect(screen.queryByText('active')).not.toBeInTheDocument();
  });

  it('humanizes every value of a multi_select field and joins them', () => {
    const entries: Entry[] = [
      {
        id: 'e1',
        title: 'Ava Persaud',
        custom_fields: { departments: ['warehouse_ops', 'night_shift'] },
      } as unknown as Entry,
    ];
    const fields: ContentProfileFieldSpec[] = [
      {
        key: 'departments',
        name: 'Departments',
        type: 'multi_select',
        enum: ['warehouse_ops', 'night_shift'],
      },
    ];

    render(
      <TableWidget
        view={view({
          columns: [{ field: 'custom_fields.departments', label: 'Departments' }],
        })}
        entries={entries}
        fields={fields}
        isLoading={false}
        onEntryOpen={noop}
      />
    );

    expect(screen.getAllByText('Warehouse ops, Night shift').length).toBeGreaterThan(0);
  });

  it('leaves a plain text field alone — humanization is opt-in to select/multi_select only', () => {
    const entries: Entry[] = [
      { id: 'e1', title: 'Ava Persaud', custom_fields: { job_title: 'warehouse supervisor' } } as unknown as Entry,
    ];
    const fields: ContentProfileFieldSpec[] = [
      { key: 'job_title', name: 'Job title', type: 'text' },
    ];

    render(
      <TableWidget
        view={view({
          columns: [{ field: 'custom_fields.job_title', label: 'Job title' }],
        })}
        entries={entries}
        fields={fields}
        isLoading={false}
        onEntryOpen={noop}
      />
    );

    expect(screen.getAllByText('warehouse supervisor').length).toBeGreaterThan(0);
  });
});
