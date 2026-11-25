import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  aiConfigurationApi,
  AI_CONFIGURATION_QUERY_KEY,
  ATTACHMENT_ANALYSIS_QUERY_KEY,
  attachmentAnalysisApi,
  REALTIME_VOICE_QUERY_KEY,
  VOICE_CONFIGURATION_QUERY_KEY,
  EMBEDDING_CONFIGURATION_QUERY_KEY,
  realtimeVoiceConfigurationApi,
  voiceConfigurationApi,
  embeddingConfigurationApi,
  type ConnectionWrite,
  type GenerationSettings,
  type ModelConnection,
  type ModelProfile,
  type ProfileWrite,
} from "../../../api/aiConfiguration";
import {
  MODEL_CREDENTIALS_QUERY_KEY,
  PROVIDER_LABELS,
  type ModelProvider,
} from "../../../api/modelCredentials";
import { defaultModel, RECOMMENDED_MODELS } from "../../../api/modelPresets";
import { errorMessageFromAxios } from "../../../api/helpers";
import { profileSummary } from "./modelSettingsSummary";
import { DeploymentLimits } from "./DeploymentLimits";
import { QuickProviderSetup } from "./QuickProviderSetup";
import { ModelCategory } from "./ModelCategory";
import { RealtimeVoiceConfiguration } from "./RealtimeVoiceConfiguration";
import { VoiceInputSection } from "./VoiceInputSection";
import { VoiceConfiguration } from "./VoiceConfiguration";
import { EmbeddingConfiguration } from "./EmbeddingConfiguration";
import { AttachmentAnalysisConfiguration } from "./AttachmentAnalysisConfiguration";
import { ModelRoutingSection } from "./ModelRoutingSection";
import { useToast } from "../../../context/ToastContext";
import { useAuthOptional } from "../../../context/AuthContext";
import { useScopeOptional } from "../../../context/ScopeContext";
import { isChatProfile, specializedModel } from "../../../api/modelProfileKinds";
import { useAIConfiguration } from "../hooks/useAIConfiguration";
import {
  AudioLines,
  Brain,
  Database,
  Mic,
  KeyRound,
  Plus,
  Check,
  AlertCircle,
  ChevronDown,
  ShieldCheck,
  Trash2,
  Pencil,
  Image,
  SlidersHorizontal,
} from "lucide-react";
import { Modal, Badge } from "../../../components/ui";
import { LINE_ICON_STROKE } from "../../../components/ui/IconWell";
import { Button } from "../../../components/ui/Button";
import { Input, Select, Stack, Surface, Text } from "../../../ui";

const SPECIALTY_MODELS: Partial<Record<ModelProvider, { id: string; label: string }[]>> = {
  openai: [
    { id: "gpt-4o-mini-transcribe", label: "GPT-4o mini transcribe (Speech to Text)" },
    { id: "gpt-4o-transcribe", label: "GPT-4o transcribe (Speech to Text)" },
    { id: "whisper-1", label: "Whisper (Speech to Text)" },
    { id: "gpt-realtime-2.1-mini", label: "GPT-Realtime 2.1 mini (Realtime Voice)" },
    { id: "gpt-realtime", label: "GPT-Realtime (Realtime Voice)" },
    { id: "gpt-realtime-mini", label: "GPT-Realtime mini (Realtime Voice)" },
    { id: "text-embedding-3-small", label: "Text embedding 3 small (Embeddings)" },
    { id: "text-embedding-3-large", label: "Text embedding 3 large (Embeddings)" },
  ],
  openrouter: [{ id: "openai/text-embedding-3-small", label: "Text embedding 3 small (Embeddings)" }],
  ollama_local: [{ id: "embeddinggemma", label: "EmbeddingGemma (Embeddings)" }],
};

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Text as="label" variant="body-sm" className="flex flex-col gap-1">
      {label}
      {children}
    </Text>
  );
}
const freshConnection = (): ConnectionWrite => ({
  name: "",
  provider: "openai",
  base_url: "",
  without_key: false,
});
const freshProfile = (): ProfileWrite => ({
  name: "",
  connection_id: "",
  model: "",
  generation_settings: {},
});

export function AiConfigurationSection({
  initialCategory,
}: { initialCategory?: "embeddings" | "speech-to-text" | "realtime-voice" } = {}) {
  const { showToast } = useToast();
  const query = useAIConfiguration();
  const userId = useAuthOptional()?.user?.id;
  const workspaceId = useScopeOptional()?.scope?.workspaceId;
  const scoped = { headers: { "X-Integral-Scope": `ws:${workspaceId}` } };
  const hasScope = Boolean(userId && workspaceId);
  const speech = useQuery({
    queryKey: [...VOICE_CONFIGURATION_QUERY_KEY, userId, workspaceId],
    queryFn: () => voiceConfigurationApi.get(scoped), enabled: hasScope,
  });
  const realtime = useQuery({
    queryKey: [...REALTIME_VOICE_QUERY_KEY, userId, workspaceId],
    queryFn: () => realtimeVoiceConfigurationApi.get(scoped), enabled: hasScope,
  });
  const images = useQuery({
    queryKey: [...ATTACHMENT_ANALYSIS_QUERY_KEY, userId, workspaceId],
    queryFn: () => attachmentAnalysisApi.get(scoped), enabled: hasScope,
  });
  const embeddings = useQuery({
    queryKey: [...EMBEDDING_CONFIGURATION_QUERY_KEY, userId, workspaceId],
    queryFn: () => embeddingConfigurationApi.get(scoped), enabled: hasScope,
  });
  const qc = useQueryClient();
  const [quickPending, setQuickPending] = useState(false);
  const [connection, setConnection] = useState<ConnectionWrite | null>(null);
  const [customModel, setCustomModel] = useState(false);
  const [profile, setProfile] = useState<ProfileWrite | null>(null);
  const [tab, setTab] = useState<"models" | "connections">("models");
  const [search, setSearch] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(Boolean(initialCategory));
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [limitsOpen, setLimitsOpen] = useState(false);
  const [category, setCategory] = useState<string | null>(initialCategory ?? null);
  useEffect(() => {
    if (!initialCategory) return;
    setAdvancedOpen(true);
    setCategory(initialCategory);
  }, [initialCategory]);
  const revealEmbeddingPending = useRef(true);
  const configurationReady = Boolean(query.data && !query.isPending && !query.isError);
  useEffect(() => {
    if (initialCategory !== "embeddings") {
      revealEmbeddingPending.current = true;
      return;
    }
    if (!configurationReady || !advancedOpen || category !== "embeddings" ||
        !revealEmbeddingPending.current) return;

    // The Settings route and its data can both resolve asynchronously.
    // Wait until the disclosed controls are mounted, then let layout settle.
    let scrollFrame = 0;
    const layoutFrame = window.requestAnimationFrame(() => {
      scrollFrame = window.requestAnimationFrame(() => {
        const target = document.getElementById("embedding-models-heading");
        if (!target || target.closest("[hidden]")) return;
        revealEmbeddingPending.current = false;
        target.focus({ preventScroll: true });
        target.scrollIntoView?.({ block: "start", inline: "nearest" });
      });
    });
    return () => {
      window.cancelAnimationFrame(layoutFrame);
      window.cancelAnimationFrame(scrollFrame);
    };
  }, [initialCategory, configurationReady, advancedOpen, category]);
  const toggleCategory = (name: string) =>
    setCategory((current) => (current === name ? null : name));
  const [removeTarget, setRemoveTarget] = useState<{
    id: string;
    name: string;
    connection: boolean;
  } | null>(null);
  const notifySuccess = (message: string) => showToast(message, "success");
  const notifyError = (message: string) => showToast(message, "error");
  const [capModel, setCapModel] = useState("");
  function modelOptions(provider: ModelProvider) {
    const options = [...RECOMMENDED_MODELS[provider], ...(SPECIALTY_MODELS[provider] ?? [])];
    for (const [role, id] of Object.entries(query.data?.model_policy?.specialists?.[provider] ?? {})) {
      if (!options.some(option => option.id === id)) options.push({ id, label: `${id} (${role})` });
    }
    return options;
  }
  const selectedConnection = query.data?.connections.find(
    (c) => c.id === profile?.connection_id,
  );
  useEffect(() => {
    const timer = setTimeout(
      () => setCapModel(profile?.model.trim() ?? ""),
      300,
    );
    return () => clearTimeout(timer);
  }, [profile?.model]);
  const specialty = profile?.embedding_only ? "Embeddings" : profile?.model && specializedModel(profile.model);
  const nonChat = Boolean(specialty);
  const caps = useQuery({
    queryKey: ["model-capabilities", selectedConnection?.provider, capModel],
    queryFn: () =>
      aiConfigurationApi.capabilities(selectedConnection!.provider, capModel),
    enabled: !!selectedConnection && !!capModel && !nonChat,
    staleTime: 300000,
  });
  const mutation = useMutation({
    mutationFn: async (work: () => Promise<unknown>) => work(),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: AI_CONFIGURATION_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: MODEL_CREDENTIALS_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: ATTACHMENT_ANALYSIS_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: ["agentive", "status"] }),
      ]);
    },
    onError: (error) =>
      notifyError(
        errorMessageFromAxios(error, "Could not save this configuration."),
      ),
  });
  const testProvider = useMutation({
    mutationFn: (id: string) => aiConfigurationApi.testConnection(id),
    onSuccess: async (result) => {
      showToast(
        result.valid
          ? "Provider connection verified."
          : result.message || "Provider test failed.",
        result.valid ? "success" : "error",
      );
      await qc.invalidateQueries({ queryKey: AI_CONFIGURATION_QUERY_KEY });
    },
    onError: (error) =>
      showToast(
        errorMessageFromAxios(error, "Could not test this provider."),
        "error",
      ),
  });
  const run = (work: () => Promise<unknown>) => mutation.mutate(work);
  const editConnection = (item: ModelConnection) =>
    setConnection({
      id: item.id,
      name: item.name,
      provider: item.provider,
      base_url: item.base_url,
      without_key: !item.key_fingerprint,
    });
  const editProfile = (item: ModelProfile) => {
    const source = query.data?.connections.find(
      (c) => c.id === item.connection_id,
    );
    setCustomModel(
      !source ||
        !modelOptions(source.provider).some((m) => m.id === item.model),
    );
    setProfile({
      id: item.id,
      name: item.name,
      connection_id: item.connection_id,
      embedding_only: item.embedding_only,
      model: item.model,
      generation_settings: { ...item.generation_settings },
    });
  };
  const changeProfileConnection = (id: string) => {
    const source = query.data?.connections.find((c) => c.id === id);
    const models = source ? RECOMMENDED_MODELS[source.provider] : [];
    setCustomModel(!models.length || !source || !defaultModel(source.provider));
    setProfile(
      (p) =>
        p && {
          ...p,
          connection_id: id,
          embedding_only: false,
          model: source ? defaultModel(source.provider) : "",
          generation_settings: {},
        },
    );
  };
  const numeric = (key: keyof GenerationSettings, text: string) =>
    setProfile(
      (p) =>
        p && {
          ...p,
          generation_settings: {
            ...p.generation_settings,
            [key]: text === "" ? undefined : Number(text),
            ...(key === "thinking_budget" && text !== ""
              ? { reasoning_effort: undefined }
              : {}),
          },
        },
    );
  if (query.isPending)
    return (
      <Surface padding="lg" border="subtle">
        <Stack gap="sm">
          <Text weight="medium">Loading your models</Text>
          <Text variant="body-sm" tone="muted">
            Getting your providers and workspace configuration…
          </Text>
        </Stack>
      </Surface>
    );
  if (query.isError || !query.data)
    return (
      <Feedback kind="error">
        AI settings could not be loaded.{" "}
        <Button variant="ghost" onClick={() => query.refetch()}>
          Try again
        </Button>
      </Feedback>
    );
  const data = query.data;
  const disabled = mutation.isPending || quickPending || data.key_policy === "platform_only" || !data.effective.editable;
  const mainProfile = data.profiles.find(p => p.id === data.default_profile_id);
  const mainConnection = data.connections.find(c => c.id === mainProfile?.connection_id);
  const embeddingPreset = mainConnection && data.model_policy?.specialists?.[mainConnection.provider]?.embedding;
  const recommendedEmbedding = data.profiles.find(p => p.connection_id === mainConnection?.id && p.model === embeddingPreset);
  const chatProfiles = data.profiles.filter(isChatProfile);
  const needle = search.trim().toLowerCase();
  const roleChoices = [
    ["Speech to Text", speech.data?.configuration],
    ["Realtime Voice", realtime.data?.configuration],
    ["Embeddings", embeddings.data?.configuration],
  ] as const;
  const assignments = roleChoices.flatMap(([role, choice]) => choice?.mode === "provider" &&
    choice.model && choice.connection_id ? [{ role, model: choice.model, connectionId: choice.connection_id }] : []);
  const legacyAssignments = assignments.filter(a => !data.profiles.some(p => p.model === a.model && p.connection_id === a.connectionId));
  const shownLegacy = legacyAssignments.filter(a => `${a.role} ${a.model}`.toLowerCase().includes(needle));
  const roleFor = (p: ModelProfile) => assignments.find(a => a.model === p.model && a.connectionId === p.connection_id)?.role;
  const catalogStatus = (p: ModelProfile) => {
    const roles: string[] = assignments.filter(a => a.model === p.model && a.connectionId === p.connection_id).map(a => a.role);
    if (p.id === data.default_profile_id) roles.unshift(data.routing?.mode === "light_heavy" ? "Heavy model" : "Main model");
    if (data.routing?.mode === "light_heavy" && p.id === data.routing.light_profile_id) roles.push("Light model");
    if (p.id === (images.data?.configuration.profile_id || images.data?.recommended_profile_id)) roles.push("Vision");
    return roles.length ? roles.join(" · ") + " · Assigned" : p.embedding_only ? "Embeddings" : specializedModel(p.model);
  };
  const profiles = data.profiles.filter((p) =>
    `${p.name} ${p.model}`.toLowerCase().includes(needle),
  );
  const connections = data.connections.filter((c) =>
    `${c.name} ${PROVIDER_LABELS[c.provider]}`.toLowerCase().includes(needle),
  );
  const addProfile = () => {
    const source = data.connections[0];
    const models = source ? RECOMMENDED_MODELS[source.provider] : [];
    setCustomModel(!models.length || !source || !defaultModel(source.provider));
    setProfile({
      ...freshProfile(),
      connection_id: source?.id ?? "",
      model: source ? defaultModel(source.provider) : "",
    });
  };
  const showConnection = () => {
    setConnection(freshConnection());
  };
  return (
    <Stack gap="lg">
      <QuickProviderSetup key={data.effective.workspace_id} data={data} disabled={mutation.isPending} onBusyChange={setQuickPending}
        assignments={{
          speech: speech.isError ? null : speech.data, realtime: realtime.isError ? null : realtime.data,
          images: images.isError ? null : images.data, embeddings: embeddings.isError ? null : embeddings.data,
        }} />
      <ModelCategory
        id="advanced-ai-configuration"
        title="Advanced Configuration"
        description="Manage providers, register models, and choose where to use them."
        icon={SlidersHorizontal}
        open={advancedOpen}
        onToggle={() => setAdvancedOpen(value => !value)}
        prominent
      >
      <div className="space-y-5">
      <ModelCategory
        id="ai-provider-model-catalog"
        title="Providers & Models"
        description="Connect providers and register models to use in your workspace."
        icon={KeyRound}
        heading="h4"
        open={catalogOpen}
        onToggle={() => setCatalogOpen(value => !value)}
      >
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border-subtle)] pb-3">
          <div
            role="tablist"
            aria-label="AI configuration"
            className="flex gap-1"
            onKeyDown={(e) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key))
                return;
              e.preventDefault();
              const next =
                e.key === "Home"
                  ? "models"
                  : e.key === "End"
                    ? "connections"
                    : tab === "models"
                      ? "connections"
                      : "models";
              setTab(next);
              setSearch("");
              e.currentTarget
                .querySelector<HTMLButtonElement>(`[data-config-tab="${next}"]`)
                ?.focus();
            }}
          >
            {(["models", "connections"] as const).map((t) => (
              <Button
                key={t}
                role="tab"
                id={`ai-${t}-tab`}
                data-config-tab={t}
                aria-selected={tab === t}
                aria-controls={`ai-${t}-panel`}
                tabIndex={tab === t ? 0 : -1}
                variant={tab === t ? "secondary" : "ghost"}
                onClick={() => {
                  setTab(t);
                  setSearch("");
                }}
              >
                {t === "models" ? "Models" : "Providers"}{" "}
                <Badge>{t === "models" ? data.profiles.length + legacyAssignments.length : data.connections.length}</Badge>
              </Button>
            ))}
          </div>
          {(data.effective.editable) && (
            <Button
              icon={<Plus size={16} />}
              disabled={disabled}
              onClick={() => {
                if (tab === "connections" || !data.connections.length)
                  showConnection();
                else addProfile();
              }}
            >
              {tab === "connections" || !data.connections.length
                ? "Connect provider"
                : "Add model"}
            </Button>
          )}
        </div>
        <div
          role="tabpanel"
          tabIndex={0}
          id={`ai-${tab}-panel`}
          aria-labelledby={`ai-${tab}-tab`}
          className="pt-4"
        >
          {tab === "models" ? (
            <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <Text weight="medium">Available models</Text>
                    {data.profiles.length > 3 && (
                      <Input
                        type="search"
                        aria-label="Search AI configuration"
                        placeholder="Find a model…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="sm:max-w-64"
                      />
                    )}
                  </div>
                  <div className="divide-y divide-[var(--border-subtle)]">
                    {profiles.map((p) => (
                      <div
                        key={p.id}
                        className="flex flex-col items-stretch justify-between gap-4 py-5 first:pt-2 sm:flex-row sm:items-center"
                      >
                        <div className="flex w-full min-w-0 flex-1 items-start gap-3 sm:w-auto">
                          <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-input)] border border-[var(--border-subtle)]">
                            <Brain size={17} strokeWidth={LINE_ICON_STROKE} />
                          </span>
                          <Stack gap="xs" className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <Text weight="medium" className="break-words">
                                {p.name}
                              </Text>
                              {catalogStatus(p) && <Badge>{catalogStatus(p)}</Badge>}
                            </div>
                            <Text
                              as="p"
                              variant="body-sm"
                              tone="muted"
                              className="break-words"
                            >
                              {p.model}
                            </Text>
                            <Text
                              as="p"
                              variant="body-sm"
                              tone="muted"
                              className="break-words"
                            >
                              {isChatProfile(p)
                                ? profileSummary(p.generation_settings)
                                : data.connections.find(c => c.id === p.connection_id)?.name ?? "Saved provider"}
                              {isChatProfile(p) && p.harness_policy && !p.harness_policy.recommended && (p.harness_policy.tier !== "light" || p.id === data.default_profile_id) && ` · ${p.harness_policy.message}`}
                            </Text>
                          </Stack>
                        </div>
                        <div className="flex flex-wrap items-center gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="min-h-11 sm:min-h-9"
                            icon={<Pencil size={14} />}
                            disabled={disabled || !!roleFor(p)}
                            title={roleFor(p) ? "Change the assignment before editing this model" : undefined}
                            onClick={() => {
                              editProfile(p);
                            }}
                          >
                            Edit profile
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="min-h-11 sm:min-h-9"
                            aria-label={`Remove ${p.name}`}
                            title={
                              p.id === data.default_profile_id
                                ? "Choose another default before removing this model"
                                : roleFor(p) ? "Change the assignment before removing this model"
                                : p.id === data.routing?.light_profile_id
                                  ? "Reassign or clear the light model before removing it"
                                  : "Remove model"
                            }
                            disabled={
                              disabled || !!roleFor(p) ||
                              p.id === data.default_profile_id ||
                              p.id === data.routing?.light_profile_id
                            }
                            onClick={() =>
                              setRemoveTarget({
                                id: p.id,
                                name: p.name,
                                connection: false,
                              })
                            }
                          >
                            <Trash2 size={16} />
                          </Button>
                        </div>
                      </div>
                    ))}
                    {shownLegacy.map(a => (
                      <div key={`${a.role}:${a.connectionId}:${a.model}`} className="flex items-start gap-3 py-5 first:pt-2">
                        <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-input)] border border-[var(--border-subtle)]">
                          <Brain size={17} strokeWidth={LINE_ICON_STROKE} />
                        </span>
                        <Stack gap="xs" className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <Text weight="medium" className="break-words">{a.model}</Text>
                            <Badge>{a.role} · Assigned</Badge>
                          </div>
                          <Text variant="body-sm" tone="muted">{data.connections.find(c => c.id === a.connectionId)?.name ?? "Saved provider"}</Text>
                        </Stack>
                      </div>
                    ))}
                    {!profiles.length && !shownLegacy.length && (
                      <EmptyState
                        icon={<Brain size={25} />}
                        title={
                          needle
                            ? "No matching models"
                            : "Your next model starts here"
                        }
                        description={
                          needle
                            ? "Try a different name or model ID."
                            : data.connections.length
                              ? "Add a model from a connected provider, then assign it below."
                              : "Connect a provider to start. You can use a local model or bring your own API key."
                        }
                      />
                    )}
                  </div>
            </div>
          ) : (
            <div>
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <Text as="p" variant="body-sm" tone="muted">
                  Store a key once and use it across your models.
                </Text>
                {data.connections.length > 3 && (
                  <Input
                    type="search"
                    aria-label="Search AI configuration"
                    placeholder="Find a provider…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="sm:max-w-64"
                  />
                )}
              </div>
              <div className="divide-y divide-[var(--border-subtle)]">
                {connections.map((c) => (
                  <div
                    key={c.id}
                    className="flex flex-col items-stretch justify-between gap-4 py-5 first:pt-2 sm:flex-row sm:items-center"
                  >
                    <div className="flex w-full min-w-0 flex-1 items-start gap-3 sm:w-auto">
                      <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-input)] border border-[var(--border-subtle)]">
                        <KeyRound size={17} strokeWidth={LINE_ICON_STROKE} />
                      </span>
                      <Stack gap="xs" className="min-w-0">
                        <Text weight="medium" className="break-words">
                          {c.name}
                        </Text>
                        <Text as="p" variant="body-sm" tone="muted">
                          {PROVIDER_LABELS[c.provider]} ·{" "}
                          {c.key_fingerprint
                            ? `Key ${c.key_fingerprint}`
                            : "No key required"}
                        </Text>
                        {c.base_url && (
                          <Text
                            as="p"
                            variant="body-sm"
                            tone="muted"
                            className="break-all"
                          >
                            {c.base_url}
                          </Text>
                        )}
                        <Text
                          as="p"
                          variant="body-sm"
                          tone={c.last_test_ok === false ? "warn" : "muted"}
                          className="flex items-center gap-1.5"
                        >
                          {c.last_test_ok === false ? (
                            <AlertCircle size={13} />
                          ) : (
                            <Check size={13} />
                          )}{" "}
                          {c.last_test_ok === false
                            ? "Last test failed"
                            : c.validated_at
                              ? "Provider verified"
                              : "Not tested"}{" "}
                          ·{" "}
                          {
                            data.profiles.filter(
                              (p) => p.connection_id === c.id,
                            ).length
                          }{" "}
                          {data.profiles.filter((p) => p.connection_id === c.id)
                            .length === 1
                            ? "model"
                            : "models"}
                        </Text>
                      </Stack>
                    </div>
                    <div className="flex flex-wrap items-center gap-1">
                      <Button
                        variant="secondary"
                        size="sm"
                        className="min-h-11 sm:min-h-9"
                        disabled={disabled || testProvider.isPending}
                        onClick={() => testProvider.mutate(c.id)}
                      >
                        {testProvider.isPending &&
                        testProvider.variables === c.id
                          ? "Testing…"
                          : "Test provider"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="min-h-11 sm:min-h-9"
                        icon={<Pencil size={14} />}
                        disabled={disabled}
                        onClick={() => {
                          editConnection(c);
                        }}
                      >
                        Edit provider
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="min-h-11 sm:min-h-9"
                        aria-label={`Remove ${c.name}`}
                        title="Remove the models using this provider first"
                        disabled={
                          disabled ||
                          data.profiles.some((p) => p.connection_id === c.id)
                        }
                        onClick={() =>
                          setRemoveTarget({
                            id: c.id,
                            name: c.name,
                            connection: true,
                          })
                        }
                      >
                        <Trash2 size={16} />
                      </Button>
                    </div>
                  </div>
                ))}
                {!connections.length && (
                  <EmptyState
                    icon={<KeyRound size={25} />}
                    title={
                      needle
                        ? "No matching providers"
                        : "Connect once. Switch models freely."
                    }
                    description={
                      needle
                        ? "Try a different connection name."
                        : "Add a provider account or local service. Your saved key stays private and can power several models."
                    }
                  />
                )}
                <Text as="p" variant="meta" tone="muted" className="pt-4">
                  Testing checks provider access. Individual model availability
                  is confirmed when you use it.
                </Text>
              </div>
            </div>
          )}
        </div>
      </ModelCategory>
      <section aria-label="Model assignments" className="space-y-3">
        <Text as="h4" variant="heading-sm">Model assignments</Text>
        <Text as="p" variant="body-sm" tone="muted">Choose which saved model each part of Integral uses.</Text>
        <ModelCategory
          id="language-models" heading="h5"
          title="Language & multimodal"
          description="Choose the models for chat, reasoning, and image understanding"
          icon={Brain}
          open={category === "language"}
          onToggle={() => toggleCategory("language")}
        >
          <ModelRoutingSection
            key={data.effective.workspace_id}
            inline
            data={{...data, profiles: chatProfiles}}
            profileEditorOpen={!!profile || mutation.isPending}
            busy={quickPending}
            onEdit={editProfile}
          />
        </ModelCategory>
              <ModelCategory
                id="embedding-models" heading="h5"
                title="Embeddings"
                description="Help search find related information"
                icon={Database}
                open={category === "embeddings"}
                onToggle={() => toggleCategory("embeddings")}
              >
                <EmbeddingConfiguration
                  connections={data.connections}
                  profiles={data.profiles}
                  recommendedProfile={recommendedEmbedding}
                  embedded
                />
              </ModelCategory>
              <ModelCategory
                id="speech-to-text-models" heading="h5"
                title="Speech to Text"
                description="Choose the model that turns speech into text"
                icon={Mic}
                open={category === "speech-to-text"}
                onToggle={() => toggleCategory("speech-to-text")}
              >
                <VoiceConfiguration
                  connections={data.connections}
                  profiles={data.profiles}
                  embedded
                  secondaryAction={<VoiceInputSection embedded />}
                />
              </ModelCategory>
              <ModelCategory id="realtime-voice-models" heading="h5" title="Realtime Voice"
                description="Two-way voice calls with an assistant" icon={AudioLines}
                open={category === "realtime-voice"} onToggle={() => toggleCategory("realtime-voice")}>
                <RealtimeVoiceConfiguration connections={data.connections} profiles={data.profiles} />
              </ModelCategory>
              <ModelCategory id="attachment-models" heading="h5" title="Images and documents"
                description="Read images and recognize visible text" icon={Image}
                open={category === "attachments"} onToggle={() => toggleCategory("attachments")}>
                <AttachmentAnalysisConfiguration key={JSON.stringify([userId, workspaceId])} profiles={data.profiles} connections={data.connections} />
              </ModelCategory>
          </section>
      </div>
      </ModelCategory>
      <ModelCategory
        id="ai-deployment-limits"
        title="Deployment Limits"
        description="Installation-wide limits for native chat turns."
        icon={ShieldCheck}
        open={limitsOpen}
        onToggle={() => setLimitsOpen(value => !value)}
        prominent
      >
        <DeploymentLimits key={JSON.stringify([userId, workspaceId])} data={data} />
      </ModelCategory>
      <Modal
        open={!!connection}
        title={connection?.id ? "Edit provider" : "Connect a provider"}
        titleIcon={<KeyRound size={19} />}
        onClose={() => {
          if (!disabled) setConnection(null);
        }}
      >
        {connection && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await aiConfigurationApi.saveConnection(connection);
                setConnection(null);
                notifySuccess("Provider saved.");
              });
            }}
          >
            <Modal.Body noSpacing>
              <Stack gap="lg">
                <Text as="p" variant="body-sm" tone="muted">
                  One provider setup can serve several models. Your saved key
                  stays on the server.
                </Text>

                <Field label="Provider name">
                  <Input
                    required
                    value={connection.name}
                    onChange={(e) =>
                      setConnection({ ...connection, name: e.target.value })
                    }
                  />
                </Field>
                <Field label="Provider">
                  <Select
                    disabled={!!connection.id}
                    value={connection.provider}
                    onChange={(e) =>
                      setConnection({
                        ...freshConnection(),
                        name: connection.name,
                        provider: e.target.value as ModelProvider,
                      })
                    }
                  >
                    {Object.entries(PROVIDER_LABELS).map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {connection.provider === "custom_openai" && (
                  <Field label="Endpoint URL">
                    <Input
                      required
                      disabled={!!connection.id}
                      value={connection.base_url}
                      onChange={(e) =>
                        setConnection({
                          ...connection,
                          base_url: e.target.value,
                        })
                      }
                      placeholder="https://provider.example/v1"
                    />
                  </Field>
                )}
                {connection.provider !== "ollama_local" && (
                  <Field
                    label={
                      connection.id
                        ? "Replacement API key (leave blank to keep saved key)"
                        : "API key"
                    }
                  >
                    <Input
                      type="password"
                      autoComplete="off"
                      disabled={connection.without_key}
                      value={connection.api_key ?? ""}
                      onChange={(e) =>
                        setConnection({
                          ...connection,
                          api_key: e.target.value,
                        })
                      }
                      required={!connection.id && !connection.without_key}
                    />
                  </Field>
                )}
                {connection.provider === "custom_openai" && (
                  <Text
                    as="label"
                    variant="body-sm"
                    className="flex items-center gap-2"
                  >
                    <input
                      type="checkbox"
                      checked={connection.without_key}
                      onChange={(e) =>
                        setConnection({
                          ...connection,
                          without_key: e.target.checked,
                          api_key: "",
                        })
                      }
                    />
                    Use without an API key
                  </Text>
                )}
              </Stack>
            </Modal.Body>
            <Modal.Footer>
              <Button
                type="button"
                variant="secondary"
                disabled={disabled}
                onClick={() => setConnection(null)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={disabled}>
                Save provider
              </Button>
            </Modal.Footer>
          </form>
        )}
      </Modal>
      <Modal
        open={!!profile}
        title={profile?.id ? "Model settings" : "Add a model"}
        titleIcon={<Brain size={19} strokeWidth={LINE_ICON_STROKE} />}
        onClose={() => {
          if (!disabled) setProfile(null);
        }}
      >
        {profile && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await aiConfigurationApi.saveProfile({
                  ...profile,
                  name: profile.name.trim() || profile.model,
                });
                setProfile(null);
                notifySuccess(
                  profile.id === data.default_profile_id
                    ? "Default profile updated. Changes apply to the next turn."
                    : "Model saved. Assign it in the matching category below.",
                );
              });
            }}
          >
            <Modal.Body noSpacing>
              <Stack gap="lg">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Profile name">
                    <Input
                      placeholder="Optional — uses the model name"
                      value={profile.name}
                      onChange={(e) =>
                        setProfile({ ...profile, name: e.target.value })
                      }
                    />
                  </Field>
                  <Field label="Provider">
                    <Select
                      value={profile.connection_id}
                      onChange={(e) => changeProfileConnection(e.target.value)}
                    >
                      {data.connections.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name} · {PROVIDER_LABELS[c.provider]}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                {selectedConnection && (
                  <Field label="Model">
                    <Select
                      value={customModel ? "__custom" : profile.model}
                      onChange={(e) => {
                        const custom = e.target.value === "__custom";
                        setCustomModel(custom);
                        setProfile({
                          ...profile,
                          model: custom ? "" : e.target.value,
                          embedding_only: false,
                          generation_settings: {},
                        });
                      }}
                    >
                      {modelOptions(selectedConnection.provider).map(
                        (m) => (
                          <option key={m.id} value={m.id}>
                            {m.label}
                          </option>
                        ),
                      )}
                      <option value="__custom">Custom model ID…</option>
                    </Select>
                  </Field>
                )}
                {customModel && (
                  <Field label="Model ID">
                    <Input
                      required
                      value={profile.model}
                      placeholder="Enter the provider's model ID"
                      onChange={(e) =>
                        setProfile({
                          ...profile,
                          model: e.target.value,
                          generation_settings: {},
                        })
                      }
                    />
                  </Field>
                )}
                {customModel && selectedConnection && selectedConnection.provider !== "anthropic" && (!specializedModel(profile.model) || profile.embedding_only) && (
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={!!profile.embedding_only}
                      onChange={e => setProfile({ ...profile, embedding_only: e.target.checked, generation_settings: {} })} />
                    <Text as="span" variant="body-sm">Use as an embedding encoder</Text>
                  </label>
                )}
                {nonChat && <Text variant="body-sm" tone="muted">Assign this model under {specialty}.</Text>}
                {!nonChat && <>
                {caps.isError && (
                  <Text as="p" variant="body-sm" tone="warn">
                    Model capabilities are unavailable. Keep provider defaults
                    or try this model again.
                  </Text>
                )}
                <div
                  className={
                    caps.data?.reasoning_efforts.length
                      ? "grid gap-4 sm:grid-cols-2"
                      : "grid gap-4"
                  }
                >
                  {caps.data?.reasoning_efforts.length ? (
                    <Field label="Reasoning effort">
                      <Select
                        value={
                          profile.generation_settings.reasoning_effort ?? ""
                        }
                        onChange={(e) =>
                          setProfile({
                            ...profile,
                            generation_settings: {
                              ...profile.generation_settings,
                              reasoning_effort: e.target.value || undefined,
                              ...(e.target.value
                                ? {
                                    thinking_budget: undefined,
                                    thinking_enabled: undefined,
                                  }
                                : {}),
                            },
                          })
                        }
                      >
                        <option value="">Provider default</option>
                        {caps.data.reasoning_efforts.map((e) => (
                          <option key={e} value={e}>
                            {(
                              {
                                none: "Off",
                                minimal: "Minimal",
                                low: "Low",
                                medium: "Medium",
                                high: "High",
                                xhigh: "Extra high",
                                max: "Max",
                              } as Record<string, string>
                            )[e] ?? e}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  ) : caps.data?.thinking_enabled ? (
                    <Field label="Thinking">
                      <Select
                        value={
                          profile.generation_settings.thinking_enabled == null
                            ? ""
                            : String(
                                profile.generation_settings.thinking_enabled,
                              )
                        }
                        onChange={(e) =>
                          setProfile({
                            ...profile,
                            generation_settings: {
                              ...profile.generation_settings,
                              thinking_enabled:
                                e.target.value === ""
                                  ? undefined
                                  : e.target.value === "true",
                              reasoning_effort: undefined,
                              thinking_budget: undefined,
                            },
                          })
                        }
                      >
                        <option value="">Provider default</option>
                        <option value="true">On</option>
                        <option value="false">Off</option>
                      </Select>
                    </Field>
                  ) : caps.data?.thinking_budget ? (
                    <Field label="Thinking budget tokens">
                      <Input
                        type="number"
                        min="1024"
                        placeholder="Provider default"
                        value={
                          profile.generation_settings.thinking_budget ?? ""
                        }
                        onChange={(e) =>
                          numeric("thinking_budget", e.target.value)
                        }
                      />
                    </Field>
                  ) : (
                    <Text variant="body-sm" tone="muted">
                      {caps.isFetching
                        ? "Checking model capabilities…"
                        : caps.data?.verified
                          ? "This model does not expose adjustable reasoning."
                          : "Reasoning controls could not be verified for this provider/model."}
                    </Text>
                  )}
                  {caps.data?.max_tokens && (
                    <Field label="Maximum output tokens">
                      <Input
                        type="number"
                        min="1"
                        step="1"
                        placeholder="Provider default"
                        value={profile.generation_settings.max_tokens ?? ""}
                        onChange={(e) => numeric("max_tokens", e.target.value)}
                      />
                    </Field>
                  )}
                </div>
                {selectedConnection &&
                  (selectedConnection.provider === "custom_openai" ||
                    (caps.data?.thinking_enabled && !caps.data.verified)) && (
                    <Text variant="body-sm" tone="muted">
                      Optional reasoning settings are sent only when selected.
                      Confirm the supported values with your endpoint; model
                      support is unverified.
                    </Text>
                  )}
                <details>
                  <summary className="cursor-pointer rounded-[var(--radius-input)] py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]">
                    <Text as="span" weight="medium">
                      Advanced settings
                    </Text>
                  </summary>
                  <Stack gap="md" className="pt-3">
                    <div className="flex flex-wrap gap-2">
                      <Badge>
                        Tools:{" "}
                        {caps.data?.tool_calling == null
                          ? "unverified"
                          : caps.data.tool_calling
                            ? "supported"
                            : "not advertised"}
                      </Badge>
                      <Badge>
                        Images:{" "}
                        {caps.data?.vision == null
                          ? "unverified"
                          : caps.data.vision
                            ? "supported"
                            : "not advertised"}
                      </Badge>
                      {caps.data && (
                        <Badge>
                          {caps.data.native_context
                            ? `${caps.data.native_context.toLocaleString()} model context tokens`
                            : "Model context capacity unavailable"}
                        </Badge>
                      )}
                    </div>
                    <AdvancedOptions
                      profile={profile}
                      capabilities={caps.data}
                      numeric={numeric}
                      onChange={setProfile}
                    />
                  </Stack>
                </details>
                </>}
              </Stack>
            </Modal.Body>
            <Modal.Footer>
              <Button
                type="button"
                variant="secondary"
                disabled={disabled}
                onClick={() => setProfile(null)}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={
                  disabled ||
                  !profile.model.trim() ||
                  (!nonChat && (caps.isFetching || capModel !== profile.model.trim()))
                }
              >
                Save profile
              </Button>
            </Modal.Footer>
          </form>
        )}
      </Modal>
      <Modal
        open={!!removeTarget}
        variant="compact"
        width="max-w-dialog-confirm"
        title={removeTarget?.connection ? "Remove provider?" : "Remove model?"}
        onClose={() => {
          if (!disabled) setRemoveTarget(null);
        }}
      >
        <Modal.Body noSpacing>
          <Stack gap="lg">
            <Text as="p">
              Remove “{removeTarget?.name}” from Integral? This does not delete
              your provider account.
            </Text>
          </Stack>
        </Modal.Body>
        <Modal.Footer>
          <Button
            variant="ghost"
            disabled={disabled}
            onClick={() => setRemoveTarget(null)}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={disabled}
            onClick={() =>
              run(async () => {
                if (!removeTarget) return;
                if (removeTarget.connection)
                  await aiConfigurationApi.removeConnection(removeTarget.id);
                else await aiConfigurationApi.removeProfile(removeTarget.id);
                setRemoveTarget(null);
                notifySuccess("Configuration removed.");
              })
            }
          >
            Remove
          </Button>
        </Modal.Footer>
      </Modal>
    </Stack>
  );
}

function Feedback({
  kind,
  children,
}: {
  kind: "error" | "success";
  children: ReactNode;
}) {
  return (
    <div
      role={kind === "error" ? "alert" : "status"}
      className="flex items-start gap-2"
    >
      <span className="mt-0.5 shrink-0">
        {kind === "error" ? <AlertCircle size={16} /> : <Check size={16} />}
      </span>
      <Text
        as="div"
        variant="body-sm"
        tone={kind === "error" ? "danger" : "default"}
      >
        {children}
      </Text>
    </div>
  );
}
function EmptyState({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-4 py-12 text-center">
      <span className="mb-1">{icon}</span>
      <Text as="h4" variant="heading-sm">
        {title}
      </Text>
      <Text as="p" variant="body-sm" tone="muted" className="max-w-md">
        {description}
      </Text>
    </div>
  );
}
function AdvancedOptions({
  profile,
  capabilities,
  numeric,
  onChange,
}: {
  profile: ProfileWrite;
  capabilities:
    import("../../../api/aiConfiguration").ModelCapabilities | undefined;
  numeric: (key: keyof GenerationSettings, value: string) => void;
  onChange: (profile: ProfileWrite) => void;
}) {
  const groups = [
    {
      title: "Response & context",
      hint: "Sampling, thinking and local context",
      fields: [
        ["temperature", "Temperature", capabilities?.temperature],
        ["top_p", "Top-p", capabilities?.top_p],
        [
          "thinking_budget",
          "Thinking token budget",
          capabilities?.thinking_budget &&
            !!capabilities.reasoning_efforts.length,
        ],
        [
          "context_window",
          "Local context window",
          capabilities?.context_window,
        ],
      ],
    },
    {
      title: "Timeouts",
      hint: "How long to wait for the provider",
      fields: [
        ["request_timeout", "Request timeout (seconds)", true],
        ["idle_timeout", "Idle timeout (seconds)", true],
      ],
    },
    {
      title: "Run limits",
      hint: "Bound the work Integral can do in a turn",
      fields: [
        ["model_request_limit", "Model requests per turn", true],
        ["tool_call_limit", "Tool calls per turn", true],
        ["turn_token_limit", "Total tokens per turn", true],
        ["turn_timeout", "Turn timeout (seconds)", true],
      ],
    },
  ] as const;
  return (
    <div className="divide-y divide-[var(--border-subtle)] border-y border-[var(--border-subtle)]">
      {groups.map((group) => (
        <details key={group.title} className="group">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]">
            <span>
              <Text as="span" weight="medium">
                {group.title}
              </Text>
              <Text
                as="span"
                variant="body-sm"
                tone="muted"
                className="block sm:ml-3 sm:inline"
              >
                {group.hint}
              </Text>
            </span>
            <ChevronDown
              size={16}
              className="shrink-0 transition-transform group-open:rotate-180"
            />
          </summary>
          <div className="pb-5">
            <div className="grid gap-4 sm:grid-cols-2">
              {group.fields
                .filter(([, , visible]) => visible)
                .map(([key, label]) => (
                  <Field key={key} label={label}>
                    <Input
                      type="number"
                      step={
                        key === "temperature" || key === "top_p" ? "0.1" : "1"
                      }
                      placeholder="Default"
                      value={profile.generation_settings[key] ?? ""}
                      onChange={(e) => numeric(key, e.target.value)}
                    />
                  </Field>
                ))}
              {group.title === "Response & context" &&
                capabilities?.verbosity && (
                  <Field label="Response detail">
                    <Select
                      value={profile.generation_settings.verbosity ?? ""}
                      onChange={(e) =>
                        onChange({
                          ...profile,
                          generation_settings: {
                            ...profile.generation_settings,
                            verbosity: e.target.value || undefined,
                          },
                        })
                      }
                    >
                      <option value="">Provider default</option>
                      <option value="low">Concise</option>
                      <option value="medium">Standard</option>
                      <option value="high">Detailed</option>
                    </Select>
                  </Field>
                )}
            </div>
            <Text as="p" variant="body-sm" tone="muted" className="mt-3">
              {group.title === "Response & context"
                ? "Leave blank for provider defaults. Choose temperature or top-p. A thinking budget must fit within the output limit."
                : group.title === "Timeouts"
                  ? "Leave blank to use deployment timeouts. A shorter idle timeout can detect a stalled request sooner."
                  : "Leave blank for deployment limits. Lower values apply to this profile; approval and access checks remain in place."}
            </Text>
          </div>
        </details>
      ))}
    </div>
  );
}
