export interface ChatAgentIdentity { key: string; name: string; description: string; icon: string; avatar?: string | null }
export const WORKSPACE_ASSISTANT: ChatAgentIdentity = {
  key: 'workspace-assistant', name: 'Integral AI', description: 'Your workspace assistant', icon: 'integral',
};
export const ALL_WORKSPACE_ASSISTANTS = 'all-workspace-assistants';
