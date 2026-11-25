import { AudioLines, Database, Image, Mic, type LucideIcon } from "lucide-react";
import type {
  AIConfiguration, AttachmentAnalysisConfiguration, EmbeddingConfiguration,
  RealtimeVoiceConfiguration, VoiceConfigurationData,
} from "../../../api/aiConfiguration";
import { Surface, Text } from "../../../ui";

export interface AssignmentDetails {
  speech?: VoiceConfigurationData | null;
  realtime?: RealtimeVoiceConfiguration | null;
  images?: AttachmentAnalysisConfiguration | null;
  embeddings?: EmbeddingConfiguration | null;
}

function visionValue(images: AttachmentAnalysisConfiguration, data: AIConfiguration) {
  if (!images.configuration.enabled) return "Off";
  if (!images.available) return "Unavailable";
  if (!images.editable) return "Workspace assignment";
  const id = images.configuration.profile_id;
  if (id) return data.profiles.find(p => p.id === id && p.supports_vision)?.model ?? "Unavailable";
  return images.recommended_model || "Automatic";
}

function Assignment({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value?: string | null }) {
  return <div className="min-w-0 space-y-1">
    <dt className="flex items-center gap-1.5">
      <Text as="span" variant="label" tone="muted" className="shrink-0"><Icon size={14} aria-hidden /></Text>
      <Text variant="label" tone="muted">{label}</Text>
    </dt>
    <Text as="dd" variant="body-sm" weight="medium" className="[overflow-wrap:anywhere]">
      {value === undefined ? "Loading…" : value ?? "Unavailable"}
    </Text>
  </div>;
}

export function QuickModelSummary({ data, speech, realtime, images, embeddings }: AssignmentDetails & { data: AIConfiguration }) {
  const routing = data.effective.routing;
  const dual = routing?.mode === "light_heavy";
  return <div role="group" aria-label="Current model assignments" className="flex flex-wrap items-stretch gap-x-6 gap-y-4">
    <Surface as="dl" padding="md" radius="input" tone="panel-2" border="none" className="flex min-w-0 flex-[1_1_11rem] flex-col justify-center gap-3">
      <div>
        <Text as="dt" variant="label" tone="muted">{dual ? "Heavy model" : "Main model"}</Text>
        <Text as="dd" variant="heading-sm" className="[overflow-wrap:anywhere]">{routing?.heavy_model || data.effective.model}</Text>
      </div>
      {dual && <div>
        <Text as="dt" variant="label" tone="muted">Light model</Text>
        <Text as="dd" variant="body-sm" weight="medium" className="[overflow-wrap:anywhere]">{routing.light_model || "Unavailable"}</Text>
      </div>}
    </Surface>
    <dl className="grid min-w-0 flex-[2_1_22rem] grid-cols-2 content-center gap-x-4 gap-y-3">
      <Assignment icon={Image} label="Images and documents" value={images && visionValue(images, data)} />
      <Assignment icon={Database} label="Embeddings" value={embeddings && (embeddings.serving_available === false ? "Unavailable" : embeddings.effective_model || "Unavailable")} />
      <Assignment icon={Mic} label="Speech to Text" value={speech && (speech.effective_model || (speech.configuration.mode === "provider" ? "Unavailable" : "Browser recognition"))} />
      <Assignment icon={AudioLines} label="Realtime Voice" value={realtime && (realtime.effective_model || (realtime.configuration.mode === "disabled" ? "Off" : "Unavailable"))} />
    </dl>
  </div>;
}
