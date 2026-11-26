import { useRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { ComposerMicButton } from '../ai-chat/components/ComposerMicButton';
import { ComposerDictationProvider, useComposerDictationActions } from './ComposerDictationContext';

const fixture = vi.hoisted(() => ({
  beginDictation: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
  stopDictation: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
  listening: vi.fn(() => false),
  toast: vi.fn(),
  queryClient: { invalidateQueries: vi.fn() },
}));

vi.mock('@assistant-ui/react', () => ({ useAuiState: () => false }));
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => fixture.queryClient }));
vi.mock('../../context/ToastContext', () => ({ useToast: () => ({ showToast: fixture.toast }) }));
vi.mock('../../api/speech', () => ({
  SPEECH_CONFIG_QUERY_ROOT: ['speech'],
  useSpeechConfig: () => ({ data: { preferences: { hotkey: 'Ctrl+Shift+Space', hotkey_mode: 'toggle' } } }),
}));
vi.mock('./useDictation', () => ({
  useDictation: () => ({
    available: true,
    status: 'idle',
    level: 0,
    error: null,
    start: fixture.beginDictation,
    stop: fixture.stopDictation,
    isListening: fixture.listening,
  }),
}));

function Harness({ onClaim }: { onClaim: (claimed: boolean) => void }) {
  const actions = useComposerDictationActions();
  const release = useRef<(() => void) | null>(null);
  return <div data-chat-surface="thread">
    <textarea aria-label="Message" ref={actions?.registerTextarea} />
    <ComposerMicButton />
    <button type="button" onClick={() => {
      release.current = actions?.acquireVoiceInput() ?? null;
      onClaim(release.current !== null);
    }}>Claim live voice</button>
    <button type="button" onClick={() => release.current?.()}>Release live voice</button>
  </div>;
}

const hotkey = { code: 'Space', key: ' ', ctrlKey: true, shiftKey: true };

it('blocks dictation from both the composer and keyboard throughout the voice lease', async () => {
  const onClaim = vi.fn();
  render(<ComposerDictationProvider><Harness onClaim={onClaim} /></ComposerDictationProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Claim live voice' }));
  expect(onClaim).toHaveBeenCalledWith(true);
  expect(screen.queryByRole('button', { name: /^Dictate/ })).toBeNull();
  fireEvent.keyDown(window, hotkey);
  expect(fixture.beginDictation).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Release live voice' }));
  expect(screen.getByRole('button', { name: /^Dictate/ })).toBeEnabled();
  fireEvent.keyDown(window, hotkey);
  expect(fixture.beginDictation).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(fixture.beginDictation).toHaveBeenCalledTimes(1));
});

it('denies voice while dictation is awaiting microphone permission', async () => {
  let grantPermission: () => void = () => {};
  fixture.beginDictation.mockImplementationOnce(() => new Promise<void>(resolve => { grantPermission = resolve; }));
  const onClaim = vi.fn();
  render(<ComposerDictationProvider><Harness onClaim={onClaim} /></ComposerDictationProvider>);
  fireEvent.click(screen.getByRole('button', { name: /^Dictate/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Claim live voice' }));
  expect(onClaim).toHaveBeenLastCalledWith(false);
  grantPermission();
  await waitFor(() => {
    fireEvent.click(screen.getByRole('button', { name: 'Claim live voice' }));
    expect(onClaim).toHaveBeenLastCalledWith(true);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Release live voice' }));
});
