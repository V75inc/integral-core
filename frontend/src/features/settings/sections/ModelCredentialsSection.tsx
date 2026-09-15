/**
 * User-facing AI model and API key settings.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Sparkles, Trash2 } from 'lucide-react';

import {
  AGENT_KEY_MODE_LABELS,
  isSpeechProvider,
  MODEL_SLOT_HINTS,
  MODEL_SLOT_LABELS,
  modelCredentialsApi,
  PROVIDER_CONSOLE_URLS,
  PROVIDER_LABELS,
  SPEECH_CAPABLE_PROVIDERS,
  type ModelCredential,
  type ModelProvider,
  type SpeechProvider,
} from '../../../api/modelCredentials';
import {
  defaultModelForSlot,
  defaultSpeechModel,
  RECOMMENDED_MODELS,
  SPEECH_MODEL_PRESETS,
  type ModelPreset,
  type ModelSlotTier,
} from '../../../api/modelPresets';
import { Button } from '../../../components/ui/Button';
import { Skeleton } from '../../../components/ui/Skeleton';
import { useToast } from '../../../context/ToastContext';
import { useAgentiveCapability } from '../hooks/useAgentiveCapability';
import { Input, Select, Stack, Surface, Text } from '../../../ui';
import { SettingsSection, StatusPill } from '../components/Field';

const QUERY_KEY = ['model-credentials', 'me'] as const;
const CUSTOM_MODEL_VALUE = '__custom__';

type OptionalSlot = 'light' | 'heavy' | 'vision';

type OptionalSlotState = {
  enabled: boolean;
  dualProvider: boolean;
  provider: ModelProvider;
  model: string;
  apiKey: string;
};

const PROVIDER_OPTIONS: { value: ModelProvider; label: string }[] = (
  Object.entries(PROVIDER_LABELS) as [ModelProvider, string][]
).map(([value, label]) => ({ value, label }));

function providerLabel(provider: ModelProvider): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

function alternateProvider(current: ModelProvider): ModelProvider {
  return PROVIDER_OPTIONS.find(o => o.value !== current)?.value ?? 'openai';
}

function freshOptionalSlot(
  slot: OptionalSlot,
  baseProvider: ModelProvider,
): OptionalSlotState {
  return {
    enabled: slot === 'light',
    dualProvider: false,
    provider: alternateProvider(baseProvider),
    model: defaultModelForSlot(baseProvider, slot),
    apiKey: '',
  };
}

function ModelSelect({
  provider,
  tier,
  value,
  onChange,
}: {
  provider: ModelProvider;
  tier: ModelSlotTier;
  value: string;
  onChange: (modelId: string) => void;
}) {
  const prevProvider = useRef(provider);

  useEffect(() => {
    if (prevProvider.current === provider) return;
    prevProvider.current = provider;
    onChange(defaultModelForSlot(provider, tier));
  }, [provider, tier, onChange]);

  return (
    <PresetModelSelect
      presets={RECOMMENDED_MODELS[provider][tier]}
      value={value}
      onChange={onChange}
    />
  );
}

/** Preset dropdown plus a "Custom model ID…" escape hatch. */
function PresetModelSelect({
  presets,
  value,
  onChange,
}: {
  presets: ModelPreset[];
  value: string;
  onChange: (modelId: string) => void;
}) {
  const presetIds = new Set(presets.map(p => p.id));
  const selectValue = presetIds.has(value) ? value : CUSTOM_MODEL_VALUE;

  return (
    <div className="flex flex-col gap-1">
      <Select
        value={selectValue}
        onChange={e => {
          const next = e.target.value;
          if (next === CUSTOM_MODEL_VALUE) {
            if (presetIds.has(value)) onChange('');
            return;
          }
          onChange(next);
        }}
      >
        {presets.map((preset: ModelPreset) => (
          <option key={preset.id} value={preset.id}>
            {preset.label}
            {preset.recommended ? ' (recommended)' : ''}
          </option>
        ))}
        <option value={CUSTOM_MODEL_VALUE}>Custom model ID…</option>
      </Select>
      {selectValue === CUSTOM_MODEL_VALUE ? (
        <Input
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder="model-id"
        />
      ) : null}
    </div>
  );
}

function ProviderSelect({
  value,
  onChange,
}: {
  value: ModelProvider;
  onChange: (provider: ModelProvider) => void;
}) {
  return (
    <Select value={value} onChange={e => onChange(e.target.value as ModelProvider)}>
      {PROVIDER_OPTIONS.map(opt => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </Select>
  );
}

function FieldLabel({ children }: { children: ReactNode }) {
  return (
    <Text variant="body-sm" tone="muted" className="mb-1 block">
      {children}
    </Text>
  );
}

function EnabledToggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2">
      <Text variant="body-sm" as="span">
        Enabled
      </Text>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label="Enabled"
        onClick={() => onChange(!checked)}
        className={`
          relative inline-flex h-5 w-9 shrink-0 items-center rounded-full
          transition-colors duration-fast
          focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)]
          ${checked ? 'bg-[var(--brand-accent)]' : 'bg-[var(--badge-muted-bg)]'}
        `}
      >
        <span
          aria-hidden
          className={`
            inline-block h-4 w-4 rounded-full bg-white shadow-[var(--shadow-sm)]
            transition-transform duration-fast
            ${checked ? 'translate-x-[18px]' : 'translate-x-[2px]'}
          `}
        />
      </button>
    </div>
  );
}

function TierPanel({
  title,
  hint,
  headerExtra,
  children,
}: {
  title: string;
  hint?: string;
  headerExtra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Surface tone="panel" border="subtle" radius="card" padding="md">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <Text variant="body" weight="medium" as="p">
            {title}
          </Text>
          {hint ? (
            <Text variant="body-sm" tone="muted" as="p" className="mt-0.5">
              {hint}
            </Text>
          ) : null}
        </div>
        {headerExtra}
      </div>
      <Stack gap="sm">{children}</Stack>
    </Surface>
  );
}

function ApiKeyRow({
  label,
  value,
  onChange,
  onTest,
  testDisabled,
  testPending,
  consoleUrl,
  consoleLabel,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onTest: () => void;
  testDisabled: boolean;
  testPending: boolean;
  consoleUrl: string;
  consoleLabel: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <FieldLabel>{label}</FieldLabel>
      <div className="flex flex-wrap gap-2">
        <Input
          type="password"
          autoComplete="off"
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder="sk-…"
          className="min-w-[12rem] flex-1"
        />
        <Button
          variant="secondary"
          size="sm"
          onClick={onTest}
          disabled={testDisabled || testPending}
        >
          Test connection
        </Button>
      </div>
      <a
        href={consoleUrl}
        target="_blank"
        rel="noreferrer"
        className="inline-flex w-fit items-center gap-1 text-xs text-[var(--accent)] hover:underline"
      >
        {consoleLabel}
        <ExternalLink size={12} />
      </a>
    </div>
  );
}

function slotSummary(
  cred: ModelCredential,
  slot: ModelSlotTier,
): string {
  if (slot === 'default') {
    return `${providerLabel(cred.provider)} · ${cred.model}`;
  }
  const model =
    slot === 'light'
      ? cred.light_model
      : slot === 'heavy'
        ? cred.heavy_model
        : cred.vision_model;
  if (!model) return '—';
  const altProvider =
    slot === 'light'
      ? cred.light_provider
      : slot === 'heavy'
        ? cred.heavy_provider
        : cred.vision_provider;
  const prov = altProvider && altProvider !== cred.provider ? altProvider : cred.provider;
  return `${providerLabel(prov)} · ${model}`;
}

function speechSummary(cred: ModelCredential): string {
  if (!cred.speech_model) return 'Off';
  const prov =
    cred.speech_provider && cred.speech_provider !== cred.provider
      ? cred.speech_provider
      : cred.provider;
  return `${providerLabel(prov)} · ${cred.speech_model}`;
}

type SpeechSlotState = {
  enabled: boolean;
  provider: SpeechProvider;
  model: string;
  apiKey: string;
};

function freshSpeechSlot(): SpeechSlotState {
  const provider = SPEECH_CAPABLE_PROVIDERS[0];
  return {
    enabled: false,
    provider,
    model: defaultSpeechModel(provider),
    apiKey: '',
  };
}

/**
 * Voice input slot. Unlike the other optional slots it is opt-in (off by
 * default — members' dictation bills this key) and its provider list is
 * limited to vendors with a streaming speech-to-text API.
 */
function SpeechSlotEditor({
  baseProvider,
  state,
  onChange,
  onValidate,
  validatePending,
}: {
  baseProvider: ModelProvider;
  state: SpeechSlotState;
  onChange: (next: SpeechSlotState) => void;
  onValidate: () => void;
  validatePending: boolean;
}) {
  const label = MODEL_SLOT_LABELS.speech;
  const needsKey = state.enabled && state.provider !== baseProvider;

  return (
    <TierPanel
      title={label}
      hint={MODEL_SLOT_HINTS.speech}
      headerExtra={
        <EnabledToggle
          checked={state.enabled}
          onChange={enabled =>
            onChange({
              ...state,
              enabled,
              model: state.model || defaultSpeechModel(state.provider),
            })
          }
        />
      }
    >
      {state.enabled ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <FieldLabel>Provider</FieldLabel>
              <Select
                value={state.provider}
                onChange={e => {
                  const next = e.target.value as SpeechProvider;
                  onChange({
                    ...state,
                    provider: next,
                    model: defaultSpeechModel(next),
                    apiKey: '',
                  });
                }}
              >
                {SPEECH_CAPABLE_PROVIDERS.map(p => (
                  <option key={p} value={p}>
                    {providerLabel(p)}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <FieldLabel>Model</FieldLabel>
              <PresetModelSelect
                presets={SPEECH_MODEL_PRESETS[state.provider]}
                value={state.model}
                onChange={model => onChange({ ...state, model })}
              />
            </div>
          </div>
          {needsKey ? (
            <ApiKeyRow
              label={`${label} API key`}
              value={state.apiKey}
              onChange={apiKey => onChange({ ...state, apiKey })}
              onTest={onValidate}
              testDisabled={!state.apiKey.trim()}
              testPending={validatePending}
              consoleUrl={PROVIDER_CONSOLE_URLS[state.provider]}
              consoleLabel={`${providerLabel(state.provider)} console`}
            />
          ) : (
            <Text variant="body-sm" tone="muted">
              Uses the same API key as your primary model.
            </Text>
          )}
        </>
      ) : (
        <Text variant="body-sm" tone="muted">
          Off — the chat mic uses your browser&apos;s built-in speech
          recognition where it&apos;s available.
        </Text>
      )}
    </TierPanel>
  );
}

function OptionalSlotEditor({
  slot,
  baseProvider,
  state,
  onChange,
  onValidate,
  validatePending,
}: {
  slot: OptionalSlot;
  baseProvider: ModelProvider;
  state: OptionalSlotState;
  onChange: (next: OptionalSlotState) => void;
  onValidate: () => void;
  validatePending: boolean;
}) {
  const tier = slot;
  const label = MODEL_SLOT_LABELS[slot];
  const hint = MODEL_SLOT_HINTS[slot];
  const modelProvider = state.dualProvider ? state.provider : baseProvider;
  const needsKey = state.enabled && state.dualProvider && state.provider !== baseProvider;

  return (
    <TierPanel
      title={label}
      hint={hint}
      headerExtra={
        <EnabledToggle
          checked={state.enabled}
          onChange={enabled =>
            onChange({
              ...state,
              enabled,
              model: enabled
                ? state.model || defaultModelForSlot(modelProvider, tier)
                : state.model,
            })
          }
        />
      }
    >
      {state.enabled ? (
        <>
          <label className="flex cursor-pointer items-start gap-2.5">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={state.dualProvider}
              onChange={e => {
                const dual = e.target.checked;
                const lp = dual ? state.provider : baseProvider;
                onChange({
                  ...state,
                  dualProvider: dual,
                  provider: dual ? state.provider : baseProvider,
                  model: defaultModelForSlot(lp, tier),
                  apiKey: dual ? state.apiKey : '',
                });
              }}
            />
            <span className="flex min-w-0 flex-col gap-1">
              <Text variant="body-sm" as="p">
                Different provider
              </Text>
              <Text variant="body-sm" tone="muted" as="p">
                Same provider reuses your primary API key.
              </Text>
            </span>
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            {state.dualProvider ? (
              <div>
                <FieldLabel>Provider</FieldLabel>
                <ProviderSelect
                  value={state.provider}
                  onChange={next =>
                    onChange({
                      ...state,
                      provider: next,
                      model: defaultModelForSlot(next, tier),
                      apiKey: '',
                    })
                  }
                />
              </div>
            ) : null}
            <div className={state.dualProvider ? '' : 'sm:col-span-2'}>
              <FieldLabel>Model</FieldLabel>
              <ModelSelect
                provider={modelProvider}
                tier={tier}
                value={state.model}
                onChange={model => onChange({ ...state, model })}
              />
            </div>
          </div>
          {needsKey ? (
            <ApiKeyRow
              label={`${label} API key`}
              value={state.apiKey}
              onChange={apiKey => onChange({ ...state, apiKey })}
              onTest={onValidate}
              testDisabled={!state.apiKey.trim()}
              testPending={validatePending}
              consoleUrl={PROVIDER_CONSOLE_URLS[state.provider]}
              consoleLabel={`${providerLabel(state.provider)} console`}
            />
          ) : (
            <Text variant="body-sm" tone="muted">
              Uses the same API key as your primary model.
            </Text>
          )}
        </>
      ) : (
        <Text variant="body-sm" tone="muted">
          Off — your primary model is used instead.
        </Text>
      )}
    </TierPanel>
  );
}

export function ModelCredentialsSection() {
  const qc = useQueryClient();
  const toast = useToast();
  const { agentKeyMode } = useAgentiveCapability();

  const [provider, setProvider] = useState<ModelProvider>('openai');
  const [model, setModel] = useState(() => defaultModelForSlot('openai', 'default'));
  const [apiKey, setApiKey] = useState('');
  const [slots, setSlots] = useState<Record<OptionalSlot, OptionalSlotState>>(() => ({
    light: freshOptionalSlot('light', 'openai'),
    heavy: freshOptionalSlot('heavy', 'openai'),
    vision: freshOptionalSlot('vision', 'openai'),
  }));
  const [speech, setSpeech] = useState<SpeechSlotState>(freshSpeechSlot);

  const credQuery = useQuery<ModelCredential | null>({
    queryKey: QUERY_KEY,
    queryFn: () => modelCredentialsApi.get(),
  });

  const active = credQuery.data ?? null;

  const activeSyncKey = active
    ? [
        active.updated_at,
        active.provider,
        active.model,
        active.light_model,
        active.heavy_model,
        active.vision_model,
        active.speech_provider,
        active.speech_model,
      ].join('|')
    : '';

  useEffect(() => {
    if (!active) return;
    setProvider(active.provider);
    setModel(active.model);
    const loadSlot = (key: OptionalSlot): OptionalSlotState => {
      const modelField =
        key === 'light'
          ? active.light_model
          : key === 'heavy'
            ? active.heavy_model
            : active.vision_model;
      const providerField =
        key === 'light'
          ? active.light_provider
          : key === 'heavy'
            ? active.heavy_provider
            : active.vision_provider;
      const separate = !!providerField && providerField !== active.provider;
      return {
        enabled: !!modelField,
        dualProvider: separate,
        provider: (providerField || alternateProvider(active.provider)) as ModelProvider,
        model: modelField || defaultModelForSlot(active.provider, key),
        apiKey: '',
      };
    };
    setSlots({
      light: loadSlot('light'),
      heavy: loadSlot('heavy'),
      vision: loadSlot('vision'),
    });
    const speechProvider: SpeechProvider = isSpeechProvider(active.speech_provider)
      ? active.speech_provider
      : isSpeechProvider(active.provider)
        ? active.provider
        : SPEECH_CAPABLE_PROVIDERS[0];
    setSpeech({
      enabled: !!active.speech_model,
      provider: speechProvider,
      model: active.speech_model || defaultSpeechModel(speechProvider),
      apiKey: '',
    });
  }, [activeSyncKey, active]);

  const applyRecommendedOpenAI = () => {
    setProvider('openai');
    setModel(defaultModelForSlot('openai', 'default'));
    setSlots({
      light: { ...freshOptionalSlot('light', 'openai'), enabled: true },
      heavy: freshOptionalSlot('heavy', 'openai'),
      vision: freshOptionalSlot('vision', 'openai'),
    });
  };

  const handleProviderChange = (next: ModelProvider) => {
    setProvider(next);
    setModel(defaultModelForSlot(next, 'default'));
    setSlots(prev => ({
      light: {
        ...prev.light,
        model: prev.light.enabled
          ? defaultModelForSlot(prev.light.dualProvider ? prev.light.provider : next, 'light')
          : prev.light.model,
      },
      heavy: {
        ...prev.heavy,
        model: prev.heavy.enabled
          ? defaultModelForSlot(prev.heavy.dualProvider ? prev.heavy.provider : next, 'heavy')
          : prev.heavy.model,
      },
      vision: {
        ...prev.vision,
        model: prev.vision.enabled
          ? defaultModelForSlot(prev.vision.dualProvider ? prev.vision.provider : next, 'vision')
          : prev.vision.model,
      },
    }));
  };

  const slotFields = (
    key: OptionalSlot,
  ): {
    model: string;
    provider: ModelProvider | undefined;
    api_key: string | undefined;
  } => {
    const s = slots[key];
    if (!s.enabled || !s.model.trim()) {
      return { model: '', provider: undefined, api_key: undefined };
    }
    const separate = s.dualProvider && s.provider !== provider;
    return {
      model: s.model.trim(),
      provider: separate ? s.provider : undefined,
      api_key: separate && s.apiKey.trim() ? s.apiKey.trim() : undefined,
    };
  };

  const saveMut = useMutation({
    mutationFn: () => {
      const light = slotFields('light');
      const heavy = slotFields('heavy');
      const vision = slotFields('vision');
      const speechModel = speech.enabled ? speech.model.trim() : '';
      const speechSeparate = !!speechModel && speech.provider !== provider;
      const trimmedKey = apiKey.trim();
      return modelCredentialsApi.upsert({
        provider,
        model: model.trim(),
        ...(trimmedKey ? { api_key: trimmedKey } : {}),
        light_model: light.model,
        light_provider: light.provider,
        light_api_key: light.api_key,
        heavy_model: heavy.model,
        heavy_provider: heavy.provider,
        heavy_api_key: heavy.api_key,
        vision_model: vision.model,
        vision_provider: vision.provider,
        vision_api_key: vision.api_key,
        speech_model: speechModel,
        speech_provider: speechSeparate ? speech.provider : undefined,
        speech_api_key:
          speechSeparate && speech.apiKey.trim() ? speech.apiKey.trim() : undefined,
      });
    },
    onSuccess: cred => {
      setApiKey('');
      setSpeech(prev => ({ ...prev, apiKey: '' }));
      setSlots(prev => ({
        light: { ...prev.light, apiKey: '' },
        heavy: { ...prev.heavy, apiKey: '' },
        vision: { ...prev.vision, apiKey: '' },
      }));
      qc.setQueryData(QUERY_KEY, cred);
      qc.invalidateQueries({ queryKey: ['agentive', 'status'] });
      toast.showToast('Models and keys saved', 'success');
    },
    onError: (err: Error) => {
      toast.showToast(err.message || 'Failed to save credentials', 'error');
    },
  });

  const validateKey = useMutation({
    mutationFn: ({ prov, key }: { prov: ModelProvider; key: string }) =>
      modelCredentialsApi.validate({ provider: prov, api_key: key }),
    onSuccess: (result, vars) => {
      toast.showToast(
        result.valid
          ? `${providerLabel(vars.prov)} key validated`
          : result.message || 'Invalid API key',
        result.valid ? 'success' : 'error',
      );
    },
    onError: (err: Error) => {
      toast.showToast(err.message || 'Validation failed', 'error');
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => modelCredentialsApi.revoke(),
    onSuccess: () => {
      qc.setQueryData(QUERY_KEY, null);
      qc.invalidateQueries({ queryKey: ['agentive', 'status'] });
      toast.showToast('Models and keys removed', 'success');
    },
    onError: (err: Error) => {
      toast.showToast(err.message || 'Failed to remove credentials', 'error');
    },
  });

  const optionalSlotValid = (key: OptionalSlot) => {
    const s = slots[key];
    if (!s.enabled) return true;
    if (!s.model.trim()) return false;
    if (s.dualProvider && s.provider !== provider && !s.apiKey.trim()) return false;
    return true;
  };

  const speechSlotValid =
    !speech.enabled ||
    (!!speech.model.trim() &&
      (speech.provider === provider || !!speech.apiKey.trim()));

  const canSave =
    (active || apiKey.trim()) &&
    model.trim() &&
    optionalSlotValid('light') &&
    optionalSlotValid('heavy') &&
    optionalSlotValid('vision') &&
    speechSlotValid;

  return (
    <SettingsSection
      title="Your models & keys"
      description="Add your provider API key and pick models for everyday chat, quick replies, complex tasks, images, and voice input."
    >
      <Stack gap="md">
        <Text variant="body-sm" tone="muted">
          Key policy:{' '}
          <Text as="span" weight="medium">
            {AGENT_KEY_MODE_LABELS[agentKeyMode] ?? agentKeyMode}
          </Text>
        </Text>

        {credQuery.isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : credQuery.isError ? (
          <Text variant="body" tone="danger" as="p">
            Failed to load model credentials:{' '}
            {credQuery.error instanceof Error
              ? credQuery.error.message
              : 'Unknown error'}
          </Text>
        ) : (
          <>
            {active ? (
              <Surface tone="panel-2" border="subtle" radius="card" padding="md">
                <Stack gap="sm">
                  <StatusPill state="ok">Active</StatusPill>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {(['default', 'light', 'heavy', 'vision'] as ModelSlotTier[]).map(
                      slot => (
                        <Surface
                          key={slot}
                          tone="panel"
                          border="subtle"
                          radius="card"
                          padding="none"
                          className="px-3 py-2"
                        >
                          <Text variant="body-sm" tone="muted" as="p">
                            {MODEL_SLOT_LABELS[slot]}
                          </Text>
                          <Text variant="body" weight="medium" as="p" className="mt-0.5">
                            {slotSummary(active, slot)}
                          </Text>
                        </Surface>
                      ),
                    )}
                    <Surface
                      tone="panel"
                      border="subtle"
                      radius="card"
                      padding="none"
                      className="px-3 py-2"
                    >
                      <Text variant="body-sm" tone="muted" as="p">
                        {MODEL_SLOT_LABELS.speech}
                      </Text>
                      <Text variant="body" weight="medium" as="p" className="mt-0.5">
                        {speechSummary(active)}
                      </Text>
                    </Surface>
                  </div>
                  <Text variant="body-sm" tone="muted">
                    Primary key {active.key_fingerprint}
                    {active.validated_at
                      ? ` · validated ${new Date(active.validated_at).toLocaleString()}`
                      : ''}
                  </Text>
                  <Button
                    variant="danger"
                    size="sm"
                    icon={<Trash2 size={14} />}
                    onClick={() => deleteMut.mutate()}
                    disabled={deleteMut.isPending}
                  >
                    Remove keys
                  </Button>
                </Stack>
              </Surface>
            ) : (
              <Text variant="body-sm" tone="muted">
                No keys saved yet. Your workspace may use shared models until you
                add your own.
              </Text>
            )}

            <Surface tone="panel-2" border="subtle" radius="card" padding="md">
              <Stack gap="md">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <Text variant="body" weight="medium">
                    {active ? 'Update models & keys' : 'Add models & keys'}
                  </Text>
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<Sparkles size={14} />}
                    onClick={applyRecommendedOpenAI}
                  >
                    Use recommended OpenAI setup
                  </Button>
                </div>

                <TierPanel
                  title={MODEL_SLOT_LABELS.default}
                  hint={MODEL_SLOT_HINTS.default}
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <FieldLabel>Provider</FieldLabel>
                      <ProviderSelect value={provider} onChange={handleProviderChange} />
                    </div>
                    <div>
                      <FieldLabel>Model</FieldLabel>
                      <ModelSelect
                        provider={provider}
                        tier="default"
                        value={model}
                        onChange={setModel}
                      />
                    </div>
                  </div>
                  <ApiKeyRow
                    label={
                      active
                        ? 'API key (leave blank to keep current)'
                        : 'API key'
                    }
                    value={apiKey}
                    onChange={setApiKey}
                    onTest={() =>
                      validateKey.mutate({ prov: provider, key: apiKey.trim() })
                    }
                    testDisabled={!apiKey.trim()}
                    testPending={validateKey.isPending}
                    consoleUrl={PROVIDER_CONSOLE_URLS[provider]}
                    consoleLabel={`${providerLabel(provider)} console`}
                  />
                </TierPanel>

                {(['light', 'heavy', 'vision'] as OptionalSlot[]).map(slot => (
                  <OptionalSlotEditor
                    key={slot}
                    slot={slot}
                    baseProvider={provider}
                    state={slots[slot]}
                    onChange={next => setSlots(prev => ({ ...prev, [slot]: next }))}
                    onValidate={() =>
                      validateKey.mutate({
                        prov: slots[slot].provider,
                        key: slots[slot].apiKey.trim(),
                      })
                    }
                    validatePending={validateKey.isPending}
                  />
                ))}

                <SpeechSlotEditor
                  baseProvider={provider}
                  state={speech}
                  onChange={setSpeech}
                  onValidate={() =>
                    validateKey.mutate({
                      prov: speech.provider,
                      key: speech.apiKey.trim(),
                    })
                  }
                  validatePending={validateKey.isPending}
                />

                <div className="flex justify-end border-t border-[var(--border-subtle)] pt-3">
                  <Button
                    size="sm"
                    onClick={() => saveMut.mutate()}
                    disabled={!canSave || saveMut.isPending}
                  >
                    Save
                  </Button>
                </div>
              </Stack>
            </Surface>
          </>
        )}
      </Stack>
    </SettingsSection>
  );
}
