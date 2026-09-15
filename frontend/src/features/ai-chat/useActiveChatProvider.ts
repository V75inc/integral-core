import { useMemo } from 'react';
import { useSettings } from '../settings/store';
import type { HarnessProviderId } from '../settings/types';
import { JvAgentProvider } from './providers/JvAgentProvider';
import { MockEchoProvider } from './providers/MockEchoProvider';
import type { ChatProvider } from './providers/types';

/**
 * Resolves the active ``ChatProvider`` from user settings.
 *
 * Two routings are exposed today — ``jvagent-embedded`` (default) and
 * ``mock-echo`` — toggled from the Agents panel in Settings. The
 * embedded routing maps to ``JvAgentProvider``; on the wire that means
 * ``provider_id === 'jvagent'`` (the backend's chat-provider registry
 * has a single ``jvagent`` adapter that auto-selects embed vs. HTTP transport).
 */
export function useActiveChatProvider(): ChatProvider {
  const [settings] = useSettings();
  const id: HarnessProviderId = settings.providers.defaultProviderId;
  return useMemo(() => resolveProvider(id), [id]);
}

function resolveProvider(id: HarnessProviderId): ChatProvider {
  switch (id) {
    case 'mock-echo':
      return MockEchoProvider;
    case 'jvagent-embedded':
    default:
      return JvAgentProvider;
  }
}
