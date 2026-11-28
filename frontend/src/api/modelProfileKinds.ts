import type { ModelProfile, RealtimeVoiceChoice } from "./aiConfiguration";

/** Conservative catalog classification, not a claim of provider capability.
 * Older profiles fall back to known model names; custom IDs remain configurable
 * as embedding encoders in Providers & Models.
 */
export function isEmbeddingProfile(profile: Pick<ModelProfile, "model" | "harness_policy" | "embedding_only">): boolean {
  if (profile.embedding_only || profile.harness_policy?.tier === "embedding") return true;
  const id = profile.model.trim().toLowerCase().split("/").pop() ?? "";
  return /^(?:gemini-embedding-|text-embedding|embeddinggemma|nomic-embed|all-minilm|mxbai-embed|embed-)/.test(id);
}

export function isTranscriptionModel(model: string): boolean {
  return ["whisper-1", "gpt-transcribe", "gpt-live-transcribe"].includes(model) || /^gpt-4o(?:-mini)?-transcribe(?:-[a-z0-9]+)*$/.test(model);
}

export function isRealtimeVoiceModel(model: string): model is Exclude<RealtimeVoiceChoice["model"], ""> {
  return ["gpt-realtime-2.1-mini", "gpt-realtime-2.1", "gpt-realtime-2", "gpt-realtime-1.5", "gpt-realtime", "gpt-realtime-mini"].includes(model);
}

/** The role catalog and chat picker must agree on older profiles without a policy tier. */
export function specializedModel(model: string) {
  const id = model.trim().toLowerCase().split("/").pop() ?? "";
  if (isTranscriptionModel(id)) return "Speech to Text";
  if (isRealtimeVoiceModel(id) || /^(?:gpt-realtime|gpt-live-1|gpt-4o(?:-mini)?-realtime-preview)(?:[-.]|$)/.test(id)) return "Realtime Voice";
  if (isEmbeddingProfile({ model })) return "Embeddings";
  return null;
}

export function isChatProfile(profile: Pick<ModelProfile, "model" | "harness_policy" | "embedding_only">): boolean {
  return !profile.embedding_only && profile.harness_policy?.tier !== "voice" &&
    profile.harness_policy?.tier !== "embedding" && !specializedModel(profile.model);
}
