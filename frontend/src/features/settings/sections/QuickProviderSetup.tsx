import { useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { aiConfigurationApi, AI_CONFIGURATION_QUERY_KEY, ATTACHMENT_ANALYSIS_QUERY_KEY, REALTIME_VOICE_QUERY_KEY, VOICE_CONFIGURATION_QUERY_KEY, realtimeVoiceConfigurationApi, voiceConfigurationApi, type AIConfiguration, type ModelConnection } from "../../../api/aiConfiguration";
import { SPEECH_CONFIG_QUERY_ROOT } from "../../../api/speech";
import { MODEL_CREDENTIALS_QUERY_KEY, PROVIDER_LABELS, type ModelProvider } from "../../../api/modelCredentials";
import { defaultModel, RECOMMENDED_MODELS } from "../../../api/modelPresets";
import { isRealtimeVoiceModel } from "../../../api/modelProfileKinds";
import { errorMessageFromAxios } from "../../../api/helpers";
import { useToast } from "../../../context/ToastContext";
import { Button } from "../../../components/ui/Button";
import { Input, Select, Stack, Surface, Text } from "../../../ui";
import { IconWell, LINE_ICON_STROKE } from "../../../components/ui/IconWell";
import { QuickModelSummary, type AssignmentDetails } from "./QuickModelSummary";
import { allowsVoiceRole } from "./voiceRoleModels";

const OPENAI_VOICE_DEFAULTS = {
  transcription: "gpt-4o-mini-transcribe",
  realtime: "gpt-realtime-2.1-mini",
} as const;
const QUICK_PROVIDERS = ["openai", "gemini", "anthropic", "openrouter", "ollama"] as const;
type QuickProvider = typeof QUICK_PROVIDERS[number];
const isQuickProvider = (provider: string): provider is QuickProvider => QUICK_PROVIDERS.some(value => value === provider);

function savedConnection(data: AIConfiguration, provider: ModelProvider) {
  const active = data.profiles.find(profile => profile.id === data.default_profile_id);
  const connection = data.connections.find(item => item.id === active?.connection_id && item.provider === provider);
  const matching = data.connections.filter(item => item.provider === provider);
  return connection || (matching.length === 1 ? matching[0] : undefined);
}

/** Minimal BYOK path; all writes use the existing authenticated configuration API. */
export function QuickProviderSetup({ data, disabled, onBusyChange, assignments }: {
  assignments?: AssignmentDetails; data: AIConfiguration; disabled: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const { showToast } = useToast();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [provider, setProvider] = useState<QuickProvider>(isQuickProvider(data.effective.provider) ? data.effective.provider : "openai");
  const [apiKey, setApiKey] = useState("");
  const progress = useRef<{ provider: QuickProvider; connectionId: string } | null>(null);
  const managed = data.key_policy === "platform_only";
  const available = Boolean(data.effective.model && data.effective.source !== "unavailable");
  const connection = savedConnection(data, provider);
  const model = data.model_policy?.providers[provider]?.preferred_heavy ?? defaultModel(provider);
  const modelName = RECOMMENDED_MODELS[provider].find(item => item.id === model)?.label || model;
  const readOnly = managed || !data.effective.editable;
  const setup = useMutation({
    mutationFn: async ({ selectedProvider, key }: { selectedProvider: QuickProvider; key: string }) => {
      const request = { headers: { "X-Integral-Scope": `ws:${data.effective.workspace_id}` } };
      const current = await aiConfigurationApi.get(request);
      if (current.effective.workspace_id !== data.effective.workspace_id) throw new Error("The workspace changed. Open AI Models and try again.");
      if (current.key_policy === "platform_only" || !current.effective.editable) throw new Error("This workspace uses its owner's AI configuration.");
      const previous = progress.current?.provider === selectedProvider ? current.connections.find(item => item.id === progress.current?.connectionId) : undefined;
      const existing = previous || savedConnection(current, selectedProvider);
      // Read the separately versioned model roles before writing anything.
      const [transcription, realtime] = selectedProvider === "openai"
        ? await Promise.all([voiceConfigurationApi.get(request), realtimeVoiceConfigurationApi.get(request)])
        : [undefined, undefined];
      const specialists = current.model_policy?.specialists?.[selectedProvider];
      const voiceDefaults = { ...OPENAI_VOICE_DEFAULTS, ...specialists };
      const active = current.profiles.find(profile => profile.id === current.default_profile_id);
      const saved: ModelConnection = await aiConfigurationApi.saveConnection({
        ...(existing ? { id: existing.id } : {}),
        name: existing?.name || PROVIDER_LABELS[selectedProvider], provider: selectedProvider,
        base_url: existing?.base_url ?? "", without_key: false, ...(key.trim() ? { api_key: key.trim() } : {}),
      }, request);
      // Clear the field once the server has validated and stored the key.
      setApiKey("");
      progress.current = { provider: selectedProvider, connectionId: saved.id };
      const registered = current.profiles.filter(p => p.connection_id === saved.id);
      async function ensureProfile(model: string, name: string, role: "main" | "embedding" | "other") {
        const existing = registered.find(p => p.model === model && (role === "embedding" || !p.embedding_only)
          && (role !== "main" || !Object.keys(p.generation_settings).length));
        if (existing) return existing;
        const added = await aiConfigurationApi.saveProfile({ name, connection_id: saved.id, model, generation_settings: {} }, request);
        registered.push(added);
        return added;
      }
      const mainModel = current.model_policy?.providers[selectedProvider]?.preferred_heavy ?? defaultModel(selectedProvider);
      const main = active?.connection_id === saved.id ? active : await ensureProfile(mainModel,
        RECOMMENDED_MODELS[selectedProvider].find(item => item.id === mainModel)?.label || mainModel, "main");
      for (const role of ["light", "vision", "embedding"] as const) {
        const model = specialists?.[role];
        if (model) await ensureProfile(model, model, role === "embedding" ? role : "other");
      }
      const registeredTranscription = transcription?.editable && allowsVoiceRole(saved, voiceDefaults.transcription, "transcription");
      const registeredRealtime = realtime?.editable && allowsVoiceRole(saved, voiceDefaults.realtime, "realtime");
      if (registeredTranscription) await ensureProfile(voiceDefaults.transcription, voiceDefaults.transcription, "other");
      if (registeredRealtime) await ensureProfile(voiceDefaults.realtime, "GPT-Realtime 2.1 mini", "other");

      // Publish assignments only once their profiles exist. Updating an active
      // account's key retains its existing routing and tuning.
      if (main !== active) {
        await aiConfigurationApi.saveRouting({
          mode: "single", heavy_profile_id: main.id, light_profile_id: "", revision: current.routing?.revision || "",
        }, request);
      }
      // Revision zero alone does not mean dictation is unused: an inherited
      // legacy or deployment credential may already be the effective model.
      // Keep that route and any explicit choice; the new model stays assignable.
      if (registeredTranscription && transcription?.revision === 0 &&
          transcription.configuration.mode === "inherit" && !transcription.effective_model) {
        await voiceConfigurationApi.save({
          mode: "provider", connection_id: saved.id, model: voiceDefaults.transcription, revision: transcription.revision,
        }, request);
      }
      if (registeredRealtime && isRealtimeVoiceModel(voiceDefaults.realtime) && realtime?.revision === 0 && realtime.configuration.mode === "disabled") {
        await realtimeVoiceConfigurationApi.save({
          mode: "provider", connection_id: saved.id, model: voiceDefaults.realtime, revision: realtime.revision,
        }, request);
      }
    },
    onMutate: () => onBusyChange(true),
    onSuccess: () => { progress.current = null; setEditing(false); showToast("Provider saved. Model availability depends on your provider account.", "success"); },
    onError: error => {
      // Keep the form available on partial success even if the refreshed top
      // card can now resolve the saved chat model.
      if (progress.current) setEditing(true);
      showToast(errorMessageFromAxios(error, "Could not finish AI setup. Check your provider and API key, then try again."), "error");
    },
    onSettled: async () => {
      // Refresh partial saves too, so retry reuses the validated provider/profile.
      try {
        await Promise.all([
          qc.invalidateQueries({ queryKey: AI_CONFIGURATION_QUERY_KEY }),
          qc.invalidateQueries({ queryKey: MODEL_CREDENTIALS_QUERY_KEY }),
          qc.invalidateQueries({ queryKey: VOICE_CONFIGURATION_QUERY_KEY }),
          qc.invalidateQueries({ queryKey: REALTIME_VOICE_QUERY_KEY }),
          qc.invalidateQueries({ queryKey: ATTACHMENT_ANALYSIS_QUERY_KEY }),
          qc.invalidateQueries({ queryKey: SPEECH_CONFIG_QUERY_ROOT }),
          qc.invalidateQueries({ queryKey: ["agentive", "status"] }),
        ]);
      } finally { onBusyChange(false); }
    },
  });
  const pending = disabled || setup.isPending;
  function submit(event: FormEvent) {
    event.preventDefault();
    setup.mutate({ selectedProvider: provider, key: apiKey });
  }
  const providerName = PROVIDER_LABELS[data.effective.provider as ModelProvider] || "Deployment provider";
  return <Surface as="section" aria-label="Quick Configuration" padding="lg" border="subtle">
    <Stack gap="md">
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <IconWell size="sm" aria-hidden className="hidden sm:inline-flex"><SlidersHorizontal size={18} strokeWidth={LINE_ICON_STROKE} /></IconWell>
          <div className="min-w-0">
            <Text as="h3" variant="heading-sm">Quick Configuration</Text>
            <Text as="p" variant="body-sm" tone="muted">
              {readOnly ? available ? `${providerName} · ${managed ? "Managed by this installation" : "Managed by the workspace owner"}`
                : managed ? "AI is managed for this installation." : "This workspace uses its owner's AI setup."
                : editing ? "Choose a provider or update its API key." : !available ? "Choose a provider and enter its API key to set up model defaults." : providerName}
            </Text>
          </div>
        </div>
        {available && !readOnly && !editing &&
          <Button type="button" variant="secondary" size="sm" className="shrink-0" disabled={pending} onClick={() => {
            setProvider(isQuickProvider(data.effective.provider) ? data.effective.provider : "openai");
            setEditing(true);
          }}>{data.effective.source === "deployment" ? "Use my own provider" : "Change provider"}</Button>}
      </header>
      {available && !editing && <QuickModelSummary data={data} {...assignments} />}
      {available && data.effective.harness_policy && !data.effective.harness_policy.recommended && <Text as="p" variant="body-sm" tone={data.effective.harness_policy.tier === "light" ? "warn" : "muted"}>{data.effective.harness_policy.message}</Text>}
      {!readOnly && (!available || editing) && <form onSubmit={submit}>
        <Stack gap="md">
          <label className="block space-y-1"><Text as="span" variant="label">AI provider</Text>
            <Select aria-label="AI provider" value={provider} disabled={pending} onChange={event => { setProvider(event.target.value as QuickProvider); setApiKey(""); progress.current = null; }}>
              {QUICK_PROVIDERS.map(item => <option key={item} value={item}>{PROVIDER_LABELS[item]}</option>)}
            </Select>
          </label>
          <label className="block space-y-1"><Text as="span" variant="label">API key</Text>
            <Input aria-label="API key" type="password" autoComplete="off" required={!connection?.key_fingerprint} value={apiKey} disabled={pending}
              placeholder={connection?.key_fingerprint ? "Leave blank to keep your saved key" : "Enter your provider API key"} onChange={event => setApiKey(event.target.value)} />
          </label>
          {provider === "gemini" && <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="underline">Get a Gemini API key from Google AI Studio</a>}
          <Text as="p" variant="meta" tone="muted">{connection && connection.id === data.profiles.find(item => item.id === data.default_profile_id)?.connection_id
            ? "Your current chat model settings will be kept." : `${modelName} will be selected automatically.${data.routing?.mode === "light_heavy" ? " This switches chat from separate Heavy and Light models to one model." : ""}`}</Text>
          <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 border-t border-[var(--border-subtle)] pt-3">
            <Text as="p" variant="meta" tone="muted" className="mr-auto">API usage is billed by your provider.</Text>
            <div className="ml-auto flex items-center justify-end gap-2">
              {editing && <Button type="button" variant="ghost" disabled={pending} onClick={() => { setEditing(false); setApiKey(""); }}>Cancel</Button>}
              <Button type="submit" disabled={pending || (!connection?.key_fingerprint && !apiKey.trim())} loading={setup.isPending}>{available ? "Save provider" : "Connect provider"}</Button>
            </div>
          </div>
        </Stack>
      </form>}
    </Stack>
  </Surface>;
}
