import { useMutation, useQueryClient } from '@tanstack/react-query';
import { entryTypesApi } from '../../../../api/entryTypes';
import {
  entryTypesForTrackQueryKey,
  trackAttachedContentProfileQueryKey,
} from '../../../../queryKeys';
import type { EntryTypeNode, ContentProfileFieldSpec } from '../../../../types';

interface SaveFieldsVars {
  entryTypeId: string;
  fields: ContentProfileFieldSpec[];
}

export function useFieldEdit(trackId: string) {
  const queryClient = useQueryClient();
  const etKey = entryTypesForTrackQueryKey(trackId);
  const cpKey = trackAttachedContentProfileQueryKey(trackId);

  const patchEntryTypeFields = (
    snapshot: EntryTypeNode[] | undefined,
    entryTypeId: string,
    fields: ContentProfileFieldSpec[]
  ): EntryTypeNode[] | undefined => {
    if (!snapshot) return snapshot;
    return snapshot.map(et =>
      et.id === entryTypeId
        ? { ...et, form_schema: { ...(et.form_schema ?? {}), fields } }
        : et
    );
  };

  const mutation = useMutation<
    EntryTypeNode,
    Error,
    SaveFieldsVars,
    { snapshot: EntryTypeNode[] | undefined }
  >({
    mutationFn: async ({ entryTypeId, fields }) => {
      const snapshot = queryClient.getQueryData<EntryTypeNode[]>(etKey);
      const existing = snapshot?.find(et => et.id === entryTypeId);
      return entryTypesApi.update(entryTypeId, {
        form_schema: { ...(existing?.form_schema ?? {}), fields },
      });
    },
    onMutate: async ({ entryTypeId, fields }) => {
      await queryClient.cancelQueries({ queryKey: etKey });
      const snapshot = queryClient.getQueryData<EntryTypeNode[]>(etKey);
      if (snapshot) {
        queryClient.setQueryData<EntryTypeNode[]>(
          etKey,
          patchEntryTypeFields(snapshot, entryTypeId, fields) ?? snapshot
        );
      }
      return { snapshot };
    },
    onError: (_err, _vars, context) => {
      if (context?.snapshot) {
        queryClient.setQueryData(etKey, context.snapshot);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: etKey });
      queryClient.invalidateQueries({ queryKey: cpKey });
    },
  });

  const setFieldsOptimistic = (entryTypeId: string, fields: ContentProfileFieldSpec[]) => {
    const snapshot = queryClient.getQueryData<EntryTypeNode[]>(etKey);
    if (!snapshot) return;
    queryClient.setQueryData<EntryTypeNode[]>(
      etKey,
      patchEntryTypeFields(snapshot, entryTypeId, fields) ?? snapshot
    );
  };

  return {
    saveFields: (entryTypeId: string, fields: ContentProfileFieldSpec[]) =>
      mutation.mutateAsync({ entryTypeId, fields }),
    setFieldsOptimistic,
    isSaving: mutation.isPending,
    error: mutation.error,
  };
}
