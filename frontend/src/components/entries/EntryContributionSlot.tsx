import {
  forwardRef,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { extensionsApi } from '../../api/extensions';
import { useScope } from '../../context/ScopeContext';
import type { OperationalModelFormSchema } from '../../types';
import {
  AppExtensionViewHost,
  type AppExtensionViewHostHandle,
} from '../extensions/AppExtensionViewHost';

export type EntryContributionPlacement = 'entry_compose' | 'entry_detail';

export type EntryContributionUiContribution = {
  placement: string;
  extension_view_key: string;
  layout?: string;
};

export type EntryContributionSlotProps = {
  placement: EntryContributionPlacement;
  appId?: string;
  formSchema?:
    | { ui_contributions?: EntryContributionUiContribution[] }
    | OperationalModelFormSchema
    | null;
  trackId?: string;
  entryId?: string;
  entryTypeKey?: string;
  mode?: 'create' | 'edit' | 'detail';
  customFields?: Record<string, unknown>;
  onDraftPatch?: (patch: {
    custom_fields?: Record<string, unknown>;
    related?: unknown;
  }) => void;
  className?: string;
};

export type EntryContributionSlotHandle = {
  requestValidate: () => Promise<{
    ok: boolean;
    error?: string;
    field_errors?: Record<string, string>;
  }>;
  requestSubmit: (entryId?: string | null) => Promise<{
    ok: boolean;
    error?: string;
    value?: unknown;
  }>;
  notifyCommitted: (entryId: string, mode?: 'create' | 'edit') => void;
  hasContribution: boolean;
};

export function resolveEntryContribution(
  formSchema: EntryContributionSlotProps['formSchema'],
  placement: EntryContributionPlacement,
): EntryContributionUiContribution | null {
  const list = formSchema?.ui_contributions;
  if (!Array.isArray(list) || list.length === 0) return null;
  const match = list.find((c) => c.placement === placement);
  if (!match?.extension_view_key) return null;
  return match;
}

export const EntryContributionSlot = forwardRef<
  EntryContributionSlotHandle,
  EntryContributionSlotProps
>(function EntryContributionSlot(props, ref) {
  const {
    placement,
    appId,
    formSchema,
    trackId,
    entryId,
    entryTypeKey,
    mode = 'detail',
    customFields,
    onDraftPatch,
    className,
  } = props;

  const contribution = useMemo(
    () => resolveEntryContribution(formSchema, placement),
    [formSchema, placement],
  );
  const viewKey = contribution?.extension_view_key || '';
  const { scope } = useScope();
  const workspaceId = scope?.workspaceId ?? '';
  const [failed, setFailed] = useState(false);
  const hostRef = useRef<AppExtensionViewHostHandle>(null);

  const handshakeQuery = useQuery({
    queryKey: ['extension-view-handshake', appId, viewKey, workspaceId],
    queryFn: () => extensionsApi.handshake(appId!, viewKey),
    enabled: Boolean(appId && viewKey && workspaceId),
    staleTime: 60_000,
    retry: false,
  });

  useImperativeHandle(
    ref,
    () => ({
      hasContribution: Boolean(contribution && appId && viewKey),
      requestValidate: async () => {
        if (!contribution || !hostRef.current) return { ok: true };
        return hostRef.current.requestValidate();
      },
      requestSubmit: async (committedEntryId) => {
        if (!contribution || !hostRef.current) return { ok: true };
        return hostRef.current.requestSubmit(committedEntryId);
      },
      notifyCommitted: (committedEntryId, committedMode) => {
        hostRef.current?.notifyCommitted(committedEntryId, committedMode);
      },
    }),
    [contribution, appId, viewKey],
  );

  if (!contribution || !appId || !viewKey) {
    return null;
  }

  if (failed || handshakeQuery.isError) {
    return null;
  }

  if (handshakeQuery.isLoading || !handshakeQuery.data) {
    return (
      <div
        className={
          className ??
          'min-h-[200px] rounded-[var(--radius-card)] bg-[var(--panel-2)] animate-pulse'
        }
      />
    );
  }

  const hs = handshakeQuery.data;
  const draftContext = {
    placement,
    mode,
    trackId,
    entryId,
    entry_type_key: entryTypeKey,
    custom_fields: customFields ?? {},
    draft: {
      custom_fields: customFields ?? {},
      entry_id: entryId ?? null,
      entry_type_key: entryTypeKey,
      mode,
      track_id: trackId,
    },
    entries: entryId
      ? [{ id: entryId, custom_fields: customFields ?? {}, type: entryTypeKey }]
      : [],
  };

  return (
    <AppExtensionViewHost
      ref={hostRef}
      appId={appId}
      viewKey={viewKey}
      workspaceId={workspaceId}
      handshakeToken={hs.handshake_token}
      packageVersion={hs.package_version}
      theme={hs.theme}
      context={draftContext}
      className={className}
      onDraftPatch={onDraftPatch}
      onError={() => setFailed(true)}
      minHeight={placement === 'entry_compose' ? 280 : 240}
    />
  );
});
