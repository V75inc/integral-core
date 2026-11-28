/**
 * Lifecycle bridge for native (Core React) entry ui_contributions.
 *
 * Extension iframes speak ``integral.extension.v1`` validate/submit via
 * postMessage. Native regions register the same imperative surface on this
 * context so ``EntryContributionSlot`` can expose one handle to compose.
 */

import { createContext, useContext } from 'react';
import type {EntryFieldMetadata} from '@integral/sdk-react';
import type { Entry } from '../../types';

export type ContributionValidateResult = {
  ok: boolean;
  error?: string;
  field_errors?: Record<string, string>;
};

export type ContributionSubmitResult = {
  ok: boolean;
  field_errors?: Record<string, string>;
  error?: string;
  value?: unknown;
  outcomeRecorded?: boolean;
};

export type ContributionLifecycleHandle = {
  requestValidate: () => Promise<ContributionValidateResult>;
  requestSubmit: (entryId?: string | null) => Promise<ContributionSubmitResult>;
};

/** Lets a detail-mode region offer explicit-save edits on a record the viewer may change. */
export type ContributionDetailEdit = {
  /** Fields projected at read time; never writable. */
  readOnlyKeys: string[];
  /** ``current`` is false when the save began in a record session since left. */
  onSaved: (entry: Entry, session: { current: boolean }) => void;
};

export type ContributionLifecycleApi = {
  mode: 'create' | 'edit' | 'detail';
  placement: 'entry_compose' | 'entry_detail';
  entryId?: string;
  trackId?: string;
  entryTypeKey?: string;
  appId?: string;
  customFields: Record<string, unknown>;
  related?: unknown;
  fields?: EntryFieldMetadata[];
  fieldErrors?: Record<string, string>;
  detailEdit?: ContributionDetailEdit;
  onDraftPatch?: (patch: {
    custom_fields?: Record<string, unknown>;
    related?: unknown;
  }) => void;
  register: (handle: ContributionLifecycleHandle) => () => void;
};

export const ContributionLifecycleContext =
  createContext<ContributionLifecycleApi | null>(null);

export function useContributionLifecycle(): ContributionLifecycleApi | null {
  return useContext(ContributionLifecycleContext);
}
