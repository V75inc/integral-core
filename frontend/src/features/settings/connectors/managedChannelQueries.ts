import { queryOptions, type UseQueryResult } from '@tanstack/react-query';
import { managedChannelsApi, type ManagedChannel, type ManagedChannelGeneration } from '../../../api/managedChannels';

export function currentManagedChannels(query: UseQueryResult<ManagedChannel[]>): ManagedChannel[] {
  return query.isSuccess && query.fetchStatus === 'idle' ? query.data : [];
}

export function managedProfileOptions(principalId: string, workspaceId: string, slug: string, generation: ManagedChannelGeneration | null) {
  return queryOptions({
    queryKey: ['connectors', 'managed-profile-configuration', principalId, workspaceId, slug,
      generation?.provision_id, generation?.route_epoch],
    queryFn: ({ signal }) => {
      if (!generation) throw new Error('Managed channel generation is unavailable');
      return managedChannelsApi.profileConfiguration(workspaceId, slug, generation, signal);
    },
    enabled: generation !== null,
    retry: false,
    refetchOnMount: 'always',
  });
}
