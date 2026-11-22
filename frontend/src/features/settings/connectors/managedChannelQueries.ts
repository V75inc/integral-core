import type { UseQueryResult } from '@tanstack/react-query';
import type { ManagedChannel } from '../../../api/managedChannels';

export function currentManagedChannels(query: UseQueryResult<ManagedChannel[]>): ManagedChannel[] {
  return query.isSuccess && query.fetchStatus === 'idle' ? query.data : [];
}
