import api from "./client";
import type { AxiosRequestConfig } from "axios";
import type { ModelProvider } from "./modelCredentials";

export const AI_CONFIGURATION_QUERY_KEY = ["ai-configuration"] as const;
export interface GenerationSettings {
  reasoning_effort?: string;
  thinking_enabled?: boolean;
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
export interface HarnessModelAssessment {
  tier: "heavy" | "light" | "unverified" | "voice" | "embedding";
  recommended: boolean;
  evidence: string;
  message: string;
}
export interface ModelProfile {
  supports_vision?: boolean;
  embedding_only?: boolean;
  harness_policy?: HarnessModelAssessment;
  id: string;
  name: string;
  connection_id: string;
  model: string;
  generation_settings: GenerationSettings;
  updated_at: string;
}
export interface ModelCapabilities {
  reasoning_efforts: string[];
  thinking_enabled?: boolean;
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
export interface ModelRouting {
  mode: "single" | "light_heavy";
  heavy_profile_id: string;
  light_profile_id: string;
  revision: string;
}
export type ChatLimitKey = "request_limit" | "tool_call_limit" | "token_limit" | "run_seconds" | "request_timeout" | "idle_timeout";
export type DeploymentLimitSettings = {
  whole_answer_protection?: boolean;
  revision: number;
  host: Record<ChatLimitKey, number>;
  overrides: Partial<Record<ChatLimitKey, number>>;
  effective: Record<ChatLimitKey, number>;
};
export interface AIConfiguration {
  model_policy?: { specialists?: Partial<Record<ModelProvider, Partial<Record<"light" | "vision" | "embedding" | "transcription" | "realtime", string>>>>; version: string; note: string; providers: Record<string, { preferred_heavy: string; models: Array<{ id: string; label: string; role: string; evidence: string; recommended: boolean }> }> };
  routing?: ModelRouting;
  connections: ModelConnection[];
  profiles: ModelProfile[];
  default_profile_id: string;
  key_policy: string;
  effective: {
    harness_policy?: HarnessModelAssessment;
    workspace_id: string;
    source: string;
    editable: boolean;
    provider: string;
    model: string;
    generation_settings: GenerationSettings;
    routing?: {
      mode: "single" | "light_heavy";
      heavy_model: string;
      light_model: string;
      heavy_generation_settings?: GenerationSettings;
      light_generation_settings?: GenerationSettings;
    };
  };
  deployment_limit_settings?: DeploymentLimitSettings;
  harness_limits: {
    tool_call_limit?: number;
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
export type ProfileWrite = Omit<ModelProfile, "id" | "updated_at" | "harness_policy" | "supports_vision"> & {
  id?: string;
};
export const aiConfigurationApi = {
  async saveDeploymentLimits(body: Pick<DeploymentLimitSettings, "revision" | "overrides" | "whole_answer_protection">): Promise<DeploymentLimitSettings> {
    return (await api.post("/admin/chat-limits", body)).data;
  },
  async get(request?: AxiosRequestConfig): Promise<AIConfiguration> {
    return (await api.get("/users/me/ai-configuration", request)).data;
  },
  async capabilities(
    provider: ModelProvider,
    model: string,
  ): Promise<ModelCapabilities> {
    return (await api.post("/users/me/model-capabilities", { provider, model }))
      .data;
  },
  async saveConnection(body: ConnectionWrite, request?: AxiosRequestConfig) {
    return (await api.post("/users/me/model-connections", body, request)).data;
  },
  async testConnection(
    id: string,
  ): Promise<{ valid: boolean; message: string }> {
    return (await api.post("/users/me/model-connections/test", { id })).data;
  },
  async removeConnection(id: string) {
    await api.post("/users/me/model-connections/remove", { id });
  },
  async saveProfile(body: ProfileWrite, request?: AxiosRequestConfig) {
    return (await api.post("/users/me/model-profiles", body, request)).data;
  },
  async saveRouting(body: ModelRouting, request?: AxiosRequestConfig) {
    return (await api.post("/users/me/model-routing", body, request)).data;
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
    routing_mode?: "inherit" | "heavy" | "profile",
    reasoning_efforts?: Partial<Record<"heavy" | "light", string | null>>,
  ): Promise<{
    profile_id: string;
    editable: boolean;
    reasoning_efforts?: Partial<Record<"heavy" | "light", string>>;
    reasoning_choices?: Partial<
      Record<
        "heavy" | "light",
        { model: string; efforts: string[]; default_effort?: string | null }
      >
    >;
    routing_mode?: "inherit" | "heavy" | "profile";
  }> {
    return (
      await api.post("/users/me/conversation-model", {
        thread_id,
        ...(profile_id !== undefined ? { profile_id } : {}),
        ...(routing_mode !== undefined ? { routing_mode } : {}),
        ...(reasoning_efforts !== undefined ? { reasoning_efforts } : {}),
      })
    ).data;
  },
};

export const EMBEDDING_CONFIGURATION_QUERY_KEY = [
  "embedding-configuration",
] as const;
export interface EmbeddingChoice {
  mode: "deployment" | "provider";
  connection_id: string;
  model: string;
  dimensions: number;
  allow_workspace_text: boolean;
}
export interface EmbeddingConfiguration {
  workspace_id: string;
  editable: boolean;
  provider_overrides_supported: boolean;
  revision: number;
  configuration: EmbeddingChoice;
  effective_provider: string;
  effective_model: string;
  processing_location?: "server" | "device" | "provider";
  dimensions: number;
  profile_id: string | null;
  rebuild_status: string;
  rebuild_phase?: string;
  pending_model?: string;
  pending_profile_id?: string | null;
  enumerated?: number | null;
  verified?: number | null;
  rebuild_error?: string;
  serving_available?: boolean;
  error: string;
  changed?: boolean;
}
export const embeddingConfigurationApi = {
  async get(request?: AxiosRequestConfig): Promise<EmbeddingConfiguration> {
    return (await api.get("/users/me/embedding-configuration", request)).data;
  },
  async save(
    body: EmbeddingChoice & {
      revision: number;
      continue_previous_provider?: boolean;
    },
    request?: AxiosRequestConfig,
  ): Promise<EmbeddingConfiguration> {
    return (await api.post("/users/me/embedding-configuration", body, request)).data;
  },
  async cancel(revision: number, request?: AxiosRequestConfig): Promise<EmbeddingConfiguration> {
    return (
      await api.post("/users/me/embedding-configuration/cancel", { revision }, request)
    ).data;
  },
  async test(
    body: EmbeddingChoice, request?: AxiosRequestConfig,
  ): Promise<{ valid: boolean; message: string }> {
    return (await api.post("/users/me/embedding-configuration/test", body, request))
      .data;
  },
};

export const VOICE_CONFIGURATION_QUERY_KEY = ["voice-configuration"] as const;
export const ATTACHMENT_ANALYSIS_QUERY_KEY = ["attachment-analysis"] as const;
export interface AttachmentAnalysisConfiguration {
  workspace_id: string;
  editable: boolean;
  revision: number;
  available: boolean;
  configuration: { enabled: boolean; profile_id?: string };
  can_choose_profile?: boolean;
  recommended_model?: string;
  recommended_profile_id?: string;
}
export const attachmentAnalysisApi = {
  async get(request?: AxiosRequestConfig): Promise<AttachmentAnalysisConfiguration> {
    return (await api.get("/users/me/attachment-analysis", request)).data;
  },
  async save(body: { enabled: boolean; revision: number; profile_id?: string }, request?: AxiosRequestConfig): Promise<AttachmentAnalysisConfiguration> {
    return (await api.post("/users/me/attachment-analysis", body, request)).data;
  },
};
export interface VoiceChoice {
  mode: "inherit" | "provider" | "disabled";
  connection_id: string;
  model: string;
}
export interface VoiceConfigurationData {
  workspace_id: string;
  editable: boolean;
  revision: number;
  configuration: VoiceChoice;
  effective_model: string;
  effective_provider: string;
}
export const voiceConfigurationApi = {
  async get(request?: AxiosRequestConfig): Promise<VoiceConfigurationData> {
    return (await api.get("/users/me/voice-configuration", request)).data;
  },
  async save(
    body: VoiceChoice & { revision: number },
    request?: AxiosRequestConfig,
  ): Promise<VoiceConfigurationData> {
    return (await api.post("/users/me/voice-configuration", body, request)).data;
  },
};

export const REALTIME_VOICE_QUERY_KEY = ["realtime-voice-configuration"] as const;
export interface RealtimeVoiceChoice {
  mode: "provider" | "disabled";
  connection_id: string;
  model: string; // The server validates its own supported model registry.
}
export interface RealtimeVoiceConfiguration {
  editable: boolean;
  revision: number;
  configuration: Partial<RealtimeVoiceChoice> & Pick<RealtimeVoiceChoice, "mode">;
  effective_model: string;
  supported_models?: string[];
}
export const realtimeVoiceConfigurationApi = {
  async get(request?: AxiosRequestConfig): Promise<RealtimeVoiceConfiguration> {
    return (await api.get("/users/me/realtime-voice-configuration", request)).data;
  },
  async save(body: RealtimeVoiceChoice & { revision: number }, request?: AxiosRequestConfig): Promise<RealtimeVoiceConfiguration> {
    return (await api.post("/users/me/realtime-voice-configuration", body, request)).data;
  },
};
