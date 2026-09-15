import type { ModelProvider, SpeechProvider } from './modelCredentials';

export type ModelSlotTier = 'default' | 'light' | 'heavy' | 'vision';

export interface ModelPreset {
  id: string;
  label: string;
  recommended?: boolean;
}

export const RECOMMENDED_MODELS: Record<
  ModelProvider,
  Record<ModelSlotTier, ModelPreset[]>
> = {
  openai: {
    default: [
      { id: 'gpt-4.1', label: 'GPT-4.1', recommended: true },
      { id: 'gpt-4o', label: 'GPT-4o' },
    ],
    light: [
      { id: 'gpt-4o-mini', label: 'GPT-4o mini', recommended: true },
      { id: 'gpt-4.1-mini', label: 'GPT-4.1 mini' },
    ],
    heavy: [
      { id: 'o3-mini', label: 'o3-mini (reasoning)', recommended: true },
      { id: 'gpt-4.1', label: 'GPT-4.1' },
    ],
    vision: [
      { id: 'gpt-4o', label: 'GPT-4o (vision)', recommended: true },
      { id: 'gpt-4.1', label: 'GPT-4.1' },
    ],
  },
  anthropic: {
    default: [
      {
        id: 'claude-sonnet-4-20250514',
        label: 'Claude Sonnet 4',
        recommended: true,
      },
      { id: 'claude-3-7-sonnet-latest', label: 'Claude 3.7 Sonnet' },
    ],
    light: [
      {
        id: 'claude-3-5-haiku-latest',
        label: 'Claude 3.5 Haiku',
        recommended: true,
      },
    ],
    heavy: [
      {
        id: 'claude-sonnet-4-20250514',
        label: 'Claude Sonnet 4',
        recommended: true,
      },
    ],
    vision: [
      {
        id: 'claude-sonnet-4-20250514',
        label: 'Claude Sonnet 4 (vision)',
        recommended: true,
      },
    ],
  },
  openrouter: {
    default: [
      { id: 'openai/gpt-4.1', label: 'OpenAI GPT-4.1', recommended: true },
    ],
    light: [
      {
        id: 'openai/gpt-4o-mini',
        label: 'OpenAI GPT-4o mini',
        recommended: true,
      },
    ],
    heavy: [
      { id: 'openai/o3-mini', label: 'OpenAI o3-mini', recommended: true },
    ],
    vision: [
      { id: 'openai/gpt-4o', label: 'OpenAI GPT-4o', recommended: true },
    ],
  },
  ollama: {
    default: [
      { id: 'gpt-oss:120b', label: 'gpt-oss 120b (cloud)', recommended: true },
    ],
    light: [
      { id: 'gpt-oss:20b', label: 'gpt-oss 20b (cloud)', recommended: true },
    ],
    heavy: [
      { id: 'gpt-oss:120b', label: 'gpt-oss 120b (cloud)', recommended: true },
    ],
    vision: [
      { id: 'gemini-3-flash-preview', label: 'Gemini 3 Flash', recommended: true },
    ],
  },
};

/**
 * Voice input (speech-to-text) models, keyed by speech-capable provider.
 * Kept apart from RECOMMENDED_MODELS: only a subset of providers can
 * transcribe, so this isn't a fifth tier of every provider's table.
 */
export const SPEECH_MODEL_PRESETS: Record<SpeechProvider, ModelPreset[]> = {
  openai: [
    {
      id: 'gpt-live-transcribe',
      label: 'GPT Live Transcribe (streams as you talk)',
      recommended: true,
    },
    { id: 'gpt-transcribe', label: 'GPT Transcribe (after each pause)' },
  ],
};

export function defaultSpeechModel(provider: SpeechProvider): string {
  const presets = SPEECH_MODEL_PRESETS[provider];
  return presets.find(p => p.recommended)?.id ?? presets[0]?.id ?? '';
}

export function defaultModelForSlot(
  provider: ModelProvider,
  tier: ModelSlotTier,
): string {
  const presets = RECOMMENDED_MODELS[provider][tier];
  return presets.find(p => p.recommended)?.id ?? presets[0]?.id ?? '';
}

/** @deprecated use defaultModelForSlot(provider, 'default') */
export function defaultModel(provider: ModelProvider): string {
  return defaultModelForSlot(provider, 'default');
}

/** @deprecated use defaultModelForSlot(provider, 'light') */
export function defaultLightModel(provider: ModelProvider): string {
  return defaultModelForSlot(provider, 'light');
}

export function isPresetModel(
  provider: ModelProvider,
  tier: ModelSlotTier,
  modelId: string,
): boolean {
  if (!modelId.trim()) return false;
  return RECOMMENDED_MODELS[provider][tier].some(p => p.id === modelId);
}
