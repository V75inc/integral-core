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
  profiles: Record<string, { draft: ProfileDraft; published?: ProfileDraft & { version: number }; test?: { offered: string[]; withheld: string[]; real_actions: number; simulated_calls: number } }>;
  catalogue: Record<Audience, Record<string, ProfileCapability>>;
  connections: { id: string; label: string; audience: Audience; verification_path?: string; publish_assignable?: boolean }[];
  bindings: Record<string, string>;
  native_chat_profiles: string[];
  created_profile?: string;
}
export interface PublicWebChatSettings {
  revision: number;
  profile_key: string | null;
  enabled: boolean;
  transport_ready: boolean;
  active: boolean;
  eligible_profiles: { key: string; name: string }[];
}
export const publicWebChatSettingsKey = (workspace: string) => ['public-web-chat-settings', workspace] as const;
const publicWebChatPath = '/workspace/agent-profiles/public-web-chat';
const scope = (workspace: string) => ({ headers: { 'X-Integral-Scope': `ws:${workspace}` } });
export const connectionProfilesApi = {
  publicWebChat: (workspace: string) => apiClient.get<PublicWebChatSettings>(publicWebChatPath, scope(workspace)).then(r => r.data),
  assignPublicWebChat: (workspace: string, revision: number, profileKey: string | null) =>
    apiClient.put<PublicWebChatSettings>(`${publicWebChatPath}/assignment`, { expected_revision: revision, profile_key: profileKey }, scope(workspace)).then(r => r.data),
  setPublicWebChatEnabled: (workspace: string, revision: number, enabled: boolean) =>
    apiClient.put<PublicWebChatSettings>(`${publicWebChatPath}/enablement`, { expected_revision: revision, enabled }, scope(workspace)).then(r => r.data),
  get: (workspace: string) => apiClient.get<ProfileSettings>('/workspace/agent-profiles', scope(workspace)).then(r => r.data),
  renameWorkspaceAssistant: (workspace: string, revision: number, name: string, settings?: ProfileAppearance & { persona?: string }) =>
    apiClient.put<ProfileSettings>('/workspace/agent-profiles/workspace-assistant', { expected_revision: revision, name, ...settings }, scope(workspace)).then(r => r.data),
  create: (workspace: string, revision: number, { key: _key, ...draft }: ProfileDraft) =>
    apiClient.post<ProfileSettings>('/workspace/agent-profiles', { expected_revision: revision, ...draft }, scope(workspace)).then(r => r.data),
  save: (workspace: string, revision: number, draft: ProfileDraft) =>
    apiClient.put<ProfileSettings>('/workspace/agent-profiles/draft', { expected_revision: revision, key: draft.key, draft }, scope(workspace)).then(r => r.data),
  setNativeChatProfiles: (workspace: string, revision: number, profiles: string[]) =>
    apiClient.put<ProfileSettings>('/workspace/agent-profiles/native-chat', { expected_revision: revision, profiles }, scope(workspace)).then(r => r.data),
  publish: (workspace: string, revision: number, key: string, offerInChat: boolean, connectionIds: string[]) =>
    apiClient.post<ProfileSettings>('/workspace/agent-profiles/publish', {
      expected_revision: revision, key, offer_in_workspace_chat: offerInChat, connection_ids: connectionIds,
    }, scope(workspace)).then(r => r.data),
  action: (workspace: string, revision: number, key: string, action: 'test' | 'publish' | 'bind', connection_id?: string) => apiClient.post<ProfileSettings>(`/workspace/agent-profiles/${action}`, { expected_revision: revision, key, ...(connection_id ? { connection_id } : {}) }, scope(workspace)).then(r => r.data),
};
