import api from "./client";
import type { ModelProvider } from "./modelCredentials";

export const AI_CONFIGURATION_QUERY_KEY = ["ai-configuration"] as const;
export interface GenerationSettings {
  reasoning_effort?: string;
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  verbosity?: string;
  thinking_budget?: number;
  context_window?: number;
  request_timeout?: number;
  idle_timeout?: number;
  model_request_limit?: number;
  tool_call_limit?: number;
  turn_token_limit?: number;
  turn_timeout?: number;
}
export interface ModelConnection {
  id: string;
  name: string;
  provider: ModelProvider;
  base_url: string;
  key_fingerprint: string;
  validated_at: string;
  updated_at: string;
  last_test_ok?: boolean | null;
  last_tested_at?: string;
}
export interface ModelProfile {
  id: string;
  name: string;
  connection_id: string;
  model: string;
  generation_settings: GenerationSettings;
  updated_at: string;
}
export interface ModelCapabilities {
  reasoning_efforts: string[];
  native_context?: number | null;
  tool_calling?: boolean | null;
  vision?: boolean | null;
  max_tokens: boolean;
  temperature: boolean;
  top_p: boolean;
  verbosity: boolean;
  thinking_budget: boolean;
  context_window: boolean;
  verified: boolean;
}
export interface AIConfiguration {
  connections: ModelConnection[];
  profiles: ModelProfile[];
  default_profile_id: string;
  key_policy: string;
  effective: {
    workspace_id: string;
    source: string;
    editable: boolean;
    provider: string;
    model: string;
    generation_settings: GenerationSettings;
  };
  harness_limits: {
    request_limit: number;
    token_limit: number;
    run_seconds: number;
    request_timeout: number;
    idle_timeout: number;
  };
}
export type ConnectionWrite = Omit<
  ModelConnection,
  | "id"
  | "key_fingerprint"
  | "validated_at"
  | "updated_at"
  | "last_test_ok"
  | "last_tested_at"
> & { id?: string; api_key?: string; without_key: boolean };
export type ProfileWrite = Omit<ModelProfile, "id" | "updated_at"> & {
  id?: string;
};
export const aiConfigurationApi = {
  async get(): Promise<AIConfiguration> {
    return (await api.get("/users/me/ai-configuration")).data;
  },
  async capabilities(
    provider: ModelProvider,
    model: string,
  ): Promise<ModelCapabilities> {
    return (await api.post("/users/me/model-capabilities", { provider, model }))
      .data;
  },
  async saveConnection(body: ConnectionWrite) {
    return (await api.post("/users/me/model-connections", body)).data;
  },
  async testConnection(
    id: string,
  ): Promise<{ valid: boolean; message: string }> {
    return (await api.post("/users/me/model-connections/test", { id })).data;
  },
  async removeConnection(id: string) {
    await api.post("/users/me/model-connections/remove", { id });
  },
  async saveProfile(body: ProfileWrite) {
    return (await api.post("/users/me/model-profiles", body)).data;
  },
  async activateProfile(id: string) {
    await api.post("/users/me/model-profiles/activate", { id });
  },
  async removeProfile(id: string) {
    await api.post("/users/me/model-profiles/remove", { id });
  },
  async conversation(
    thread_id: string,
    profile_id?: string,
  ): Promise<{ profile_id: string; editable: boolean }> {
    return (
      await api.post("/users/me/conversation-model", {
        thread_id,
        ...(profile_id !== undefined ? { profile_id } : {}),
      })
    ).data;
  },
};
