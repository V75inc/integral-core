import apiClient from './client';
import type { ChatAgentIdentity } from './aiChat';
export type ProfileIcon = 'integral' | 'book' | 'search' | 'message';
export type Audience = 'public' | 'staff';
export interface ProfileAppearance { icon?: ProfileIcon; avatar?: string | null }
export interface ProfileDraft extends ProfileAppearance { key: string; audience?: Audience; name: string; description?: string; persona: string; tools: string[]; omissions: string[] }
export interface ProfileCapability {
  label: string; description: string; kind?: 'skill' | 'tool'; human_only?: boolean;
  source?: 'core' | 'app' | 'workspace'; app_id?: string; app_name?: string; skill_id?: string;
}
export interface ProfileSettings {
  revision: number;
  workspace_assistant?: ChatAgentIdentity & { persona?: string };
  profiles: Record<string, {
    draft: ProfileDraft;
    published?: ProfileDraft & { version: number };
    /** Verified exact published version; absent does not mean ready. */
    service_identity_ready?: boolean;
    test?: { offered: string[]; withheld: string[]; real_actions: number; simulated_calls: number };
  }>;
  catalogue: Record<Audience, Record<string, ProfileCapability>>;
  connections: { id: string; label: string; audience: Audience; verification_path?: string; publish_assignable?: boolean }[];
  bindings: Record<string, string>;
  native_chat_profiles: string[];
  created_profile?: string;
}
const scope = (workspace: string) => ({ headers: { 'X-Integral-Scope': `ws:${workspace}` } });
export const connectionProfilesApi = {
  get: (workspace: string) => apiClient.get<ProfileSettings>('/workspace/agent-profiles', scope(workspace)).then(r => r.data),
  renameWorkspaceAssistant: (workspace: string, revision: number, name: string, settings?: ProfileAppearance & { persona?: string }) =>
    apiClient.put<ProfileSettings>('/workspace/agent-profiles/workspace-assistant', { expected_revision: revision, name, ...settings }, scope(workspace)).then(r => r.data),
  create: (workspace: string, revision: number, { key: _key, ...draft }: ProfileDraft) =>
    apiClient.post<ProfileSettings>('/workspace/agent-profiles', { expected_revision: revision, ...draft }, scope(workspace)).then(r => r.data),
  save: (workspace: string, revision: number, draft: ProfileDraft) =>
    apiClient.put<ProfileSettings>('/workspace/agent-profiles/draft', { expected_revision: revision, key: draft.key, draft }, scope(workspace)).then(r => r.data),
  setNativeChatProfiles: (workspace: string, revision: number, profiles: string[]) =>
    apiClient.put<ProfileSettings>('/workspace/agent-profiles/native-chat', { expected_revision: revision, profiles }, scope(workspace)).then(r => r.data),
  publish: (workspace: string, revision: number, key: string, offerInChat: boolean, connectionIds: string[],
    enablePublicServiceIdentity?: true) =>
    apiClient.post<ProfileSettings>('/workspace/agent-profiles/publish', {
      expected_revision: revision, key, offer_in_workspace_chat: offerInChat, connection_ids: connectionIds,
      ...(enablePublicServiceIdentity === true ? { enable_public_service_identity: true } : {}),
    }, scope(workspace)).then(r => r.data),
  action: (workspace: string, revision: number, key: string, action: 'test' | 'publish' | 'bind', connection_id?: string) => apiClient.post<ProfileSettings>(`/workspace/agent-profiles/${action}`, { expected_revision: revision, key, ...(connection_id ? { connection_id } : {}) }, scope(workspace)).then(r => r.data),
};
