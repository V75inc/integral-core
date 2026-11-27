/**
 * Dictation state for one chat composer.
 *
 * Two contexts on purpose: the full state (status, 20 Hz level) for the mic
 * button, and a stable actions object for the textarea wrapper — otherwise
 * every level tick would re-render the tag-highlighting composer.
 */
import { useAuiState } from '@assistant-ui/react';
import { useQueryClient } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

import { SPEECH_CONFIG_QUERY_ROOT, useSpeechConfig } from '../../api/speech';
import { useToast } from '../../context/ToastContext';
import {
  CHAT_SURFACE_ATTR,
  isActiveSurface,
} from '../ai-chat/components/useTypeAnywhereComposer';
import { formatHotkey } from './hotkey';
import { useDictation, type DictationController, type StopCause } from './useDictation';
import { useDictationHotkey } from './useDictationHotkey';

const DEFAULT_HOTKEY = 'Mod+Shift+Space';

// Microphone activity is shared by every composer in this browser tab.
let voiceOwner: symbol | null = null;
const voiceInputListeners = new Set<() => void>();
const dictationActivityChecks = new Set<() => boolean>();
const voiceInputBlocked = () => voiceOwner !== null;

function subscribeVoiceInput(listener: () => void) {
  voiceInputListeners.add(listener);
  return () => { voiceInputListeners.delete(listener); };
}

function claimVoiceInput(): (() => void) | null {
  if (voiceOwner !== null) return null;
  const owner = Symbol('live-voice');
  voiceOwner = owner;
  voiceInputListeners.forEach(listener => listener());
  return () => {
    if (voiceOwner !== owner) return;
    voiceOwner = null;
    voiceInputListeners.forEach(listener => listener());
  };
}

export interface ComposerDictation extends DictationController {
  /** Hotkey as shown to the user, e.g. `⇧⌘Space`. */
  hotkeyLabel: string;
  voiceInputBlocked?: boolean;
}

export interface ComposerDictationActions {
  registerTextarea: (el: HTMLTextAreaElement | null) => void;
  /** The composer's real send (entity refs flushed); used for auto-send. */
  registerSubmit: (fn: (() => void) | null) => void;
  /** Submit through the chat composer so an active dictation run can finish first. */
  submit: () => void;
  isListening: () => boolean;
  acquireVoiceInput: () => (() => void) | null;
  stop: (cause?: StopCause) => Promise<void>;
}

export const ComposerDictationContext = createContext<ComposerDictation | null>(null);
export const ComposerDictationActionsContext =
  createContext<ComposerDictationActions | null>(null);

export function useComposerDictation(): ComposerDictation | null {
  return useContext(ComposerDictationContext);
}

export function useComposerDictationActions(): ComposerDictationActions | null {
  return useContext(ComposerDictationActionsContext);
}

export function ComposerDictationProvider({ children }: { children: ReactNode }) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const submitRef = useRef<(() => void) | null>(null);
  const isRunning = useAuiState(s => s.thread.isRunning);
  const runningRef = useRef(isRunning);
  runningRef.current = isRunning;

  const { data: config } = useSpeechConfig();
  const { showToast } = useToast();
  const qc = useQueryClient();

  const controller = useDictation({
    config,
    getTextarea: () => textareaRef.current,
    submit: () => submitRef.current?.(),
    isThreadRunning: () => runningRef.current,
  });

  const { error } = controller;
  useEffect(() => {
    if (!error) return;
    showToast(error.message, 'error');
    // A stale config offered a provider the server no longer has.
    if (error.code === 'not-configured') {
      void qc.invalidateQueries({ queryKey: SPEECH_CONFIG_QUERY_ROOT });
    }
  }, [error, qc, showToast]);

  const hotkey = config?.preferences.hotkey ?? DEFAULT_HOTKEY;
  const { start, stop, isListening, available } = controller;
  const blockedByVoice = useSyncExternalStore(subscribeVoiceInput, voiceInputBlocked, () => false);
  const startingDictation = useRef(false);
  const isDictating = useCallback(() => startingDictation.current || isListening(), [isListening]);
  useEffect(() => {
    dictationActivityChecks.add(isDictating);
    return () => { dictationActivityChecks.delete(isDictating); };
  }, [isDictating]);
  const acquireVoiceInput = useCallback(() => {
    return isDictating() || [...dictationActivityChecks].some(check => check())
      ? null : claimVoiceInput();
  }, [isDictating]);
  const guardedStart = useCallback(async () => {
    if (voiceInputBlocked() || startingDictation.current) return;
    startingDictation.current = true;
    try { await start(); }
    finally { startingDictation.current = false; }
  }, [start]);
  const guardedToggle = useCallback(() => {
    if (voiceInputBlocked()) return;
    if (isListening()) void stop('user');
    else void guardedStart();
  }, [guardedStart, isListening, stop]);
  useDictationHotkey({
    hotkey,
    mode: config?.preferences.hotkey_mode ?? 'hold_or_toggle',
    enabled: available && !blockedByVoice,
    isActive: () => {
      const el = textareaRef.current;
      return !!el && isActiveSurface(el.closest<HTMLElement>(`[${CHAT_SURFACE_ATTR}]`));
    },
    isListening,
    start: () => void guardedStart(),
    stop: () => void stop('user'),
  });

  const registerTextarea = useCallback((el: HTMLTextAreaElement | null) => {
    textareaRef.current = el;
  }, []);
  const registerSubmit = useCallback((fn: (() => void) | null) => {
    submitRef.current = fn;
  }, []);
  const submit = useCallback(() => submitRef.current?.(), []);

  const hotkeyLabel = useMemo(() => formatHotkey(hotkey), [hotkey]);
  const value = useMemo(() => ({
    ...controller, hotkeyLabel, voiceInputBlocked: blockedByVoice, start: guardedStart, toggle: guardedToggle,
  }), [controller, hotkeyLabel, blockedByVoice, guardedStart, guardedToggle]);
  const actions = useMemo(
    () => ({ registerTextarea, registerSubmit, submit, isListening, stop, acquireVoiceInput }),
    [registerTextarea, registerSubmit, submit, isListening, stop, acquireVoiceInput],
  );

  return (
    <ComposerDictationActionsContext.Provider value={actions}>
      <ComposerDictationContext.Provider value={value}>{children}</ComposerDictationContext.Provider>
    </ComposerDictationActionsContext.Provider>
  );
}
