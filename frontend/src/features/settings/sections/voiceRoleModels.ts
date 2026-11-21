import type { ModelConnection, ModelProfile, RealtimeVoiceChoice } from "../../../api/aiConfiguration";

export function isTranscriptionModel(model: string): boolean {
  return ["whisper-1", "gpt-transcribe", "gpt-live-transcribe"].includes(model) || /^gpt-4o(?:-mini)?-transcribe(?:-[a-z0-9]+)*$/.test(model);
}

export function isRealtimeVoiceModel(model: string): model is Exclude<RealtimeVoiceChoice["model"], ""> {
  return ["gpt-realtime-2.1-mini", "gpt-realtime", "gpt-realtime-mini"].includes(model);
}

export type VoiceRole = "transcription" | "realtime";

export function allowsVoiceRole(connection: ModelConnection | undefined, model: string, role: VoiceRole): boolean {
  if (connection?.provider !== "openai") return false;
  if (role === "transcription") return isTranscriptionModel(model);
  return isRealtimeVoiceModel(model) && ["", "https://api.openai.com", "https://api.openai.com/v1"].includes(
    (connection.base_url ?? "").replace(/\/+$/, ""),
  );
}

export function voiceRoleOptions(
  connections: ModelConnection[],
  profiles: ModelProfile[],
  role: VoiceRole,
  current?: { mode: string; connection_id?: string; model?: string },
) {
  const options = profiles.flatMap(profile => {
    const connection = connections.find(item => item.id === profile.connection_id);
    return allowsVoiceRole(connection, profile.model, role)
      ? [{ id: `profile:${profile.id}`, label: `${profile.name} · ${connection?.name}`, connectionId: profile.connection_id, model: profile.model }]
      : [];
  });
  if (current?.mode === "provider" && current.model && current.connection_id) {
    const registered = options.find(option => option.model === current.model && option.connectionId === current.connection_id);
    const active = registered ?? {
      id: "legacy", label: `${current.model} (current assignment)`,
      connectionId: current.connection_id, model: current.model,
    };
    return { options: registered ? options : [active, ...options], selected: active.id };
  }
  return { options, selected: current?.mode ?? "" };
}
