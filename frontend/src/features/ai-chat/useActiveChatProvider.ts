import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSettings } from '../settings/store';
import { aiChatApi } from '../../api/aiChat';
import type { HarnessProviderId } from '../settings/types';
import { JvAgentProvider } from './providers/JvAgentProvider';
import { IntegralNativeProvider } from './providers/IntegralNativeProvider';
import { MockEchoProvider } from './providers/MockEchoProvider';
import type { ChatProvider } from './providers/types';

/**
 * Resolves the active ``ChatProvider`` from user settings.
 *
 * Routings are selected from the Agents panel. The native Pydantic route is
 * used only while the backend reports it available. The embedded route maps
 * to ``JvAgentProvider``; on the wire that means ``provider_id === 'jvagent'``
 * (the backend adapter chooses embedded or HTTP transport).
 */
export function useActiveChatProvider(): ChatProvider {
  const [settings] = useSettings();
  const id: HarnessProviderId = settings.providers.defaultProviderId;
  const providers = useQuery({
    queryKey: ['chat-providers'],
    queryFn: () => aiChatApi.listProviders(),
    staleTime: 30_000,
  });
  const nativeAvailable = providers.data?.some(
    provider => provider.id === 'integral_native' && provider.available,
  ) ?? false;
  const effectiveId =
    id === 'pydantic-ai-native' &&
    providers.isFetched &&
    !nativeAvailable
      ? 'jvagent-embedded'
      : id;
  return useMemo(() => resolveProvider(effectiveId), [effectiveId]);
}

function resolveProvider(id: HarnessProviderId): ChatProvider {
  switch (id) {
    case 'mock-echo':
      return MockEchoProvider;
    case 'pydantic-ai-native':
      return IntegralNativeProvider;
    case 'jvagent-embedded':
    default:
      return JvAgentProvider;
  }
}
