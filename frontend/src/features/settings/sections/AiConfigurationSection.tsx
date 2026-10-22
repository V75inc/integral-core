import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  aiConfigurationApi,
  AI_CONFIGURATION_QUERY_KEY,
  type ConnectionWrite,
  type GenerationSettings,
  type ModelConnection,
  type ModelProfile,
  type ProfileWrite,
} from "../../../api/aiConfiguration";
import {
  modelCredentialsApi,
  MODEL_CREDENTIALS_QUERY_KEY,
  PROVIDER_LABELS,
  type ModelProvider,
} from "../../../api/modelCredentials";
import { RECOMMENDED_MODELS } from "../../../api/modelPresets";
import { errorMessageFromAxios } from "../../../api/helpers";
import { useAIConfiguration } from "../hooks/useAIConfiguration";
import {
  BrainCircuit,
  KeyRound,
  Plus,
  SlidersHorizontal,
  Check,
  AlertCircle,
  ChevronDown,
  Mic,
  ShieldCheck,
  Trash2,
  Pencil,
} from "lucide-react";
import { Modal, Badge } from "../../../components/ui";
import { LINE_ICON_STROKE } from "../../../components/ui/IconWell";
import { Button } from "../../../components/ui/Button";
import { Input, Select, Stack, Surface, Text } from "../../../ui";

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

function profileSummary(settings: GenerationSettings): string {
  const parts = [
    settings.reasoning_effort ? `${settings.reasoning_effort} reasoning` : "",
    settings.max_tokens
      ? `${settings.max_tokens.toLocaleString()} max output tokens`
      : "",
    settings.context_window
      ? `${settings.context_window.toLocaleString()} context tokens`
      : "",
    settings.thinking_budget
      ? `${settings.thinking_budget.toLocaleString()} thinking tokens`
      : "",
    settings.temperature != null ? `Temperature ${settings.temperature}` : "",
    settings.top_p != null ? `Top-p ${settings.top_p}` : "",
    settings.verbosity ? `${settings.verbosity} detail` : "",
    settings.request_timeout
      ? `${settings.request_timeout}s request limit`
      : "",
    settings.idle_timeout ? `${settings.idle_timeout}s idle limit` : "",
    settings.model_request_limit
      ? `${settings.model_request_limit} model requests per turn`
      : "",
    settings.tool_call_limit
      ? `${settings.tool_call_limit} tool calls per turn`
      : "",
    settings.turn_token_limit
      ? `${settings.turn_token_limit.toLocaleString()} total tokens per turn`
      : "",
    settings.turn_timeout ? `${settings.turn_timeout}s per turn` : "",
  ].filter(Boolean);
  return (
    parts.slice(0, 3).join(" · ") +
      (parts.length > 3 ? ` · +${parts.length - 3} more` : "") ||
    "Provider defaults"
  );
}

export function AiConfigurationSection() {
  const query = useAIConfiguration();
  const qc = useQueryClient();
  const [connection, setConnection] = useState<ConnectionWrite | null>(null);
  const [customModel, setCustomModel] = useState(false);
  const [profile, setProfile] = useState<ProfileWrite | null>(null);
  const [tab, setTab] = useState<"models" | "connections">("models");
  const [search, setSearch] = useState("");
  const [removeTarget, setRemoveTarget] = useState<{
    id: string;
    name: string;
    connection: boolean;
  } | null>(null);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState("");
  const [capModel, setCapModel] = useState("");
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
  const caps = useQuery({
    queryKey: ["model-capabilities", selectedConnection?.provider, capModel],
    queryFn: () =>
      aiConfigurationApi.capabilities(selectedConnection!.provider, capModel),
    enabled: !!selectedConnection && !!capModel,
    staleTime: 300000,
  });
  const mutation = useMutation({
    mutationFn: async (work: () => Promise<unknown>) => work(),
    onMutate: () => {
      setFailure("");
      setNotice("");
    },
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: AI_CONFIGURATION_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: MODEL_CREDENTIALS_QUERY_KEY }),
        qc.invalidateQueries({ queryKey: ["agentive", "status"] }),
      ]);
    },
    onError: (error) =>
      setFailure(
        errorMessageFromAxios(error, "Could not save this configuration."),
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
        !RECOMMENDED_MODELS[source.provider].some((m) => m.id === item.model),
    );
    setProfile({
      id: item.id,
      name: item.name,
      connection_id: item.connection_id,
      model: item.model,
      generation_settings: { ...item.generation_settings },
    });
  };
  const changeProfileConnection = (id: string) => {
    const source = query.data?.connections.find((c) => c.id === id);
    const models = source ? RECOMMENDED_MODELS[source.provider] : [];
    setCustomModel(!models.length);
    setProfile(
      (p) =>
        p && {
          ...p,
          connection_id: id,
          model: models[0]?.id ?? "",
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
  const disabled = mutation.isPending || data.key_policy === "platform_only";
  const defaultProfile = data.effective.editable
    ? data.profiles.find((p) => p.id === data.default_profile_id)
    : undefined;
  const defaultConnection = data.connections.find(
    (c) => c.id === defaultProfile?.connection_id,
  );
  const needle = search.trim().toLowerCase();
  const profiles = data.profiles.filter((p) =>
    `${p.name} ${p.model}`.toLowerCase().includes(needle),
  );
  const connections = data.connections.filter((c) =>
    `${c.name} ${PROVIDER_LABELS[c.provider]}`.toLowerCase().includes(needle),
  );
  const addProfile = () => {
    const source = data.connections[0];
    const models = source ? RECOMMENDED_MODELS[source.provider] : [];
    setCustomModel(!models.length);
    setProfile({
      ...freshProfile(),
      connection_id: source?.id ?? "",
      model: models[0]?.id ?? "",
    });
  };
  const showConnection = () => {
    setFailure("");
    setConnection(freshConnection());
  };
  return (
    <Stack gap="lg">
      {failure && !connection && !profile && !removeTarget && (
        <Feedback kind="error">{failure}</Feedback>
      )}
      {notice && <Feedback kind="success">{notice}</Feedback>}
      <Surface tone="panel" padding="lg" border="subtle" radius="card">
        <div className="flex flex-col items-start justify-between gap-5 sm:flex-row">
          <div className="flex w-full min-w-0 flex-1 items-start gap-4 sm:w-auto">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--radius-input)] border border-[var(--border-subtle)]">
              <BrainCircuit size={22} strokeWidth={LINE_ICON_STROKE} />
            </span>
            <Stack gap="xs" className="min-w-0">
              <Text variant="label" tone="muted">
                IN USE IN THIS WORKSPACE
              </Text>
              <Text as="h3" variant="heading-md" className="break-words">
                {data.effective.model || "Choose your first model"}
              </Text>
              <Text as="p" variant="body-sm" tone="muted">
                {defaultProfile
                  ? `${defaultProfile.name}${defaultConnection ? ` · ${defaultConnection.name}` : ""}`
                  : data.effective.source === "deployment"
                    ? "Managed by your deployment"
                    : data.effective.source === "workspace_owner"
                      ? "Managed by the workspace owner"
                      : "Connect a provider, then choose a default model."}
              </Text>
              {!!data.effective.model && (
                <Text
                  as="p"
                  variant="body-sm"
                  tone="muted"
                  className="break-words"
                >
                  {profileSummary(data.effective.generation_settings)}
                </Text>
              )}
            </Stack>
          </div>
          {defaultProfile && (
            <Button
              variant="secondary"
              className="min-h-11 sm:min-h-9"
              icon={<SlidersHorizontal size={16} />}
              disabled={disabled}
              onClick={() => {
                setFailure("");
                editProfile(defaultProfile);
              }}
            >
              Model settings
            </Button>
          )}
        </div>
        {!data.effective.editable && (
          <Text as="p" variant="body-sm" tone="muted" className="mt-4">
            {data.key_policy === "platform_only"
              ? "Your deployment manages AI configuration. Personal changes are disabled."
              : "Uses the workspace owner's model. Changes to your personal profiles do not change this workspace."}
          </Text>
        )}
      </Surface>
      <section aria-label="Your AI configuration">
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
                {t === "models" ? "Models" : "Providers"}
                <Badge>
                  {t === "models"
                    ? data.profiles.length
                    : data.connections.length}
                </Badge>
              </Button>
            ))}
          </div>
          <Button
            icon={<Plus size={16} />}
            disabled={disabled}
            onClick={() => {
              setFailure("");
              if (tab === "connections" || !data.connections.length)
                showConnection();
              else addProfile();
            }}
          >
            {tab === "connections" || !data.connections.length
              ? "Connect provider"
              : "Add model"}
          </Button>
        </div>
        <div
          role="tabpanel"
          tabIndex={0}
          id={`ai-${tab}-panel`}
          aria-labelledby={`ai-${tab}-tab`}
          className="pt-4"
        >
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <Text as="p" variant="body-sm" tone="muted">
              {tab === "models"
                ? "Choose a default or keep different model setups for different work."
                : "Store a key once and use it across your models."}
            </Text>
            {(tab === "models"
              ? data.profiles.length
              : data.connections.length) > 3 && (
              <Input
                type="search"
                aria-label="Search AI configuration"
                placeholder={
                  tab === "models" ? "Find a model…" : "Find a provider…"
                }
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="sm:max-w-64"
              />
            )}
          </div>
          {tab === "models" ? (
            <div className="divide-y divide-[var(--border-subtle)]">
              {profiles.map((p) => (
                <div
                  key={p.id}
                  className="flex flex-col items-stretch justify-between gap-4 py-5 first:pt-2 sm:flex-row sm:items-center"
                >
                  <div className="flex w-full min-w-0 flex-1 items-start gap-3 sm:w-auto">
                    <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-input)] border border-[var(--border-subtle)]">
                      <BrainCircuit size={17} strokeWidth={LINE_ICON_STROKE} />
                    </span>
                    <Stack gap="xs" className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Text weight="medium" className="break-words">
                          {p.name}
                        </Text>
                        {p.id === data.default_profile_id && (
                          <Badge>Default</Badge>
                        )}
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
                        {profileSummary(p.generation_settings)}
                      </Text>
                    </Stack>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    {p.id !== data.default_profile_id && (
                      <Button
                        variant="secondary"
                        size="sm"
                        className="min-h-11 sm:min-h-9"
                        disabled={disabled}
                        onClick={() =>
                          run(async () => {
                            await aiConfigurationApi.activateProfile(p.id);
                            setNotice("Default model updated.");
                          })
                        }
                      >
                        Use as default
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      className="min-h-11 sm:min-h-9"
                      icon={<Pencil size={14} />}
                      disabled={disabled}
                      onClick={() => {
                        setFailure("");
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
                          : "Remove model"
                      }
                      disabled={disabled || p.id === data.default_profile_id}
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
              {!profiles.length && (
                <EmptyState
                  icon={<BrainCircuit size={25} />}
                  title={
                    needle
                      ? "No matching models"
                      : "Your next model starts here"
                  }
                  description={
                    needle
                      ? "Try a different name or model ID."
                      : data.connections.length
                        ? "Add a model to one of your providers, choose its settings, then make it your default."
                        : "Connect a provider to start. You can use a local model or bring your own API key."
                  }
                />
              )}
            </div>
          ) : (
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
                          data.profiles.filter((p) => p.connection_id === c.id)
                            .length
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
                      disabled={disabled}
                      onClick={() =>
                        run(async () => {
                          const result =
                            await aiConfigurationApi.testConnection(c.id);
                          if (!result.valid) throw new Error(result.message);
                          setNotice(
                            c.key_fingerprint
                              ? "Provider verified using the saved key."
                              : "Provider verified.",
                          );
                        })
                      }
                    >
                      Test provider
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="min-h-11 sm:min-h-9"
                      icon={<Pencil size={14} />}
                      disabled={disabled}
                      onClick={() => {
                        setFailure("");
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
                Testing checks provider access. Individual model availability is
                confirmed when you use it.
              </Text>
            </div>
          )}
        </div>
      </section>
      <VoiceConfiguration onError={setFailure} disabled={disabled} />
      <details className="group border-t border-[var(--border-subtle)] pt-4">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-[var(--radius-input)] py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]">
          <span className="flex items-center gap-3">
            <ShieldCheck size={18} strokeWidth={LINE_ICON_STROKE} />
            <Text weight="medium">Deployment limits</Text>
          </span>
          <ChevronDown
            size={16}
            className="transition-transform group-open:rotate-180"
          />
        </summary>
        <div className="pt-4">
          <Text as="p" variant="body-sm" tone="muted">
            Up to {data.harness_limits.request_limit} model requests, 32 tool
            calls, {data.harness_limits.token_limit.toLocaleString()} total
            tokens and {Math.round(data.harness_limits.run_seconds / 60)}{" "}
            minutes per turn. Model profiles can use lower limits. Approval and
            access checks always apply.
          </Text>
        </div>
      </details>
      <Modal
        open={!!connection}
        placement="right"
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
                setNotice("Provider saved.");
              });
            }}
          >
            <Modal.Body noSpacing>
              <Stack gap="lg">
                <Text as="p" variant="body-sm" tone="muted">
                  One provider setup can serve several models. Your saved key
                  stays on the server.
                </Text>
                {failure && <Feedback kind="error">{failure}</Feedback>}
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
        placement="right"
        title={profile?.id ? "Model settings" : "Add a model"}
        titleIcon={<SlidersHorizontal size={19} />}
        onClose={() => {
          if (!disabled) setProfile(null);
        }}
      >
        {profile && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await aiConfigurationApi.saveProfile(profile);
                setProfile(null);
                setNotice(
                  profile.id === data.default_profile_id
                    ? "Default profile updated. Changes apply to the next turn."
                    : "Profile saved. Select Use as default when you want to switch models.",
                );
              });
            }}
          >
            <Modal.Body noSpacing>
              <Stack gap="lg">
                {failure && <Feedback kind="error">{failure}</Feedback>}
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Profile name">
                    <Input
                      required
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
                          generation_settings: {},
                        });
                      }}
                    >
                      {RECOMMENDED_MODELS[selectedConnection.provider].map(
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
                  {caps.data?.native_context ? (
                    <Badge>
                      {caps.data.native_context.toLocaleString()} input tokens
                    </Badge>
                  ) : null}
                </div>
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
                              } as Record<string, string>
                            )[e] ?? e}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  ) : (
                    <Text variant="body-sm" tone="muted">
                      {caps.isFetching
                        ? "Checking model capabilities…"
                        : "This model uses its default reasoning settings."}
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
                <AdvancedOptions
                  profile={profile}
                  capabilities={caps.data}
                  numeric={numeric}
                  onChange={setProfile}
                />
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
                  caps.isFetching ||
                  capModel !== profile.model.trim()
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
            {failure && <Feedback kind="error">{failure}</Feedback>}
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
                setNotice("Configuration removed.");
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
    | import("../../../api/aiConfiguration").ModelCapabilities
    | undefined;
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
          capabilities?.thinking_budget,
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

function VoiceConfiguration({
  onError,
  disabled,
}: {
  onError: (message: string) => void;
  disabled: boolean;
}) {
  const qc = useQueryClient();
  const current = useQuery({
    queryKey: MODEL_CREDENTIALS_QUERY_KEY,
    queryFn: modelCredentialsApi.get,
  });
  const [enabled, setEnabled] = useState(false);
  const [model, setModel] = useState("gpt-4o-mini-transcribe");
  const [key, setKey] = useState("");
  useEffect(() => {
    if (current.data) {
      setEnabled(!!current.data.speech_model);
      setModel(current.data.speech_model || "gpt-4o-mini-transcribe");
    }
  }, [current.data]);
  const save = useMutation({
    mutationFn: async () => {
      const c = current.data;
      if (!c) throw new Error("Choose a default chat model first.");
      return modelCredentialsApi.upsert({
        provider: c.provider,
        model: c.model,
        ...(c.base_url ? { base_url: c.base_url } : {}),
        speech_model: enabled ? model : "",
        ...(enabled
          ? {
              speech_provider: "openai" as const,
              ...(key.trim() ? { speech_api_key: key.trim() } : {}),
            }
          : {}),
      });
    },
    onSuccess: () => {
      setKey("");
      qc.invalidateQueries({ queryKey: MODEL_CREDENTIALS_QUERY_KEY });
    },
    onError: (error) =>
      onError(errorMessageFromAxios(error, "Could not save voice settings.")),
  });
  return (
    <details className="group border-t border-[var(--border-subtle)] pt-4">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-[var(--radius-input)] py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring-color)]">
        <span className="flex items-center gap-3">
          <Mic size={18} strokeWidth={LINE_ICON_STROKE} />
          <span>
            <Text as="span" weight="medium">
              Voice input
            </Text>
            <Text as="span" variant="body-sm" tone="muted" className="ml-3">
              {current.data?.speech_model
                ? "Transcription enabled"
                : "Optional transcription"}
            </Text>
          </span>
        </span>
        <ChevronDown
          size={16}
          className="transition-transform group-open:rotate-180"
        />
      </summary>
      <div className="pt-4">
        <Stack gap="md">
          <Text variant="body-sm" tone="muted">
            Optional OpenAI transcription is separate from your chat model.
            Saved voice keys are retained when you edit a model.
          </Text>
          <Text
            as="label"
            variant="body-sm"
            className="flex items-center gap-2"
          >
            <input
              type="checkbox"
              checked={enabled}
              disabled={disabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            Use provider transcription
          </Text>
          {enabled && (
            <>
              <Field label="Transcription model">
                <Input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                />
              </Field>
              <Field label="Voice API key (leave blank to keep saved key)">
                <Input
                  type="password"
                  autoComplete="off"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </Field>
            </>
          )}
          <Button
            variant="secondary"
            disabled={disabled || !current.data || save.isPending}
            onClick={() => save.mutate()}
          >
            Save voice settings
          </Button>
        </Stack>
      </div>
    </details>
  );
}
