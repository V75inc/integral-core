import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { requestOpenCompanionChat } from '../../chatHandoff';
import { AuiTaggableComposer } from '../AuiTaggableComposer';

const state = vi.hoisted(() => ({
  thread: null as string | null,
  ready: false,
  text: '',
  setText: vi.fn(),
  send: vi.fn(),
}));
const composer = { setText: state.setText, send: state.send };
const aui = { composer: () => composer };
vi.mock('@assistant-ui/react', () => ({
  useAui: () => aui,
  useAuiState: () => state.text,
}));
vi.mock('../../AIChatSurface', () => ({
  useChatActivity: () => ({ activeThreadId: state.thread, composerReady: state.ready }),
}));
vi.mock('../../../../context/ChatEntityRefsContext', () => ({
  useChatEntityRefsOptional: () => null,
}));
vi.mock('../../../speech/ComposerDictationContext', () => ({
  useComposerDictationActions: () => null,
}));
vi.mock('../../../../components/chat/TaggableComposer', () => ({
  TaggableComposer: ({ value, onChange }: { value: string; onChange: (value: string) => void }) =>
    <textarea aria-label="Message input" value={value} onChange={event => onChange(event.target.value)} />,
}));

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
  state.thread = null;
  state.ready = false;
  state.text = '';
  vi.clearAllMocks();
});
afterEach(() => vi.useRealTimers());

describe('contextual composer draft handoff', () => {
  it('retains the draft while selection takes longer than one second, then applies after reset', () => {
    requestOpenCompanionChat({ draftText: 'Help me explore my idea.' });
    const view = render(<AuiTaggableComposer />);
    act(() => vi.advanceTimersByTime(5000));
    expect(state.setText).not.toHaveBeenCalled();
    state.thread = 'restored-thread';
    state.ready = true;
    view.rerender(<AuiTaggableComposer />);
    expect(state.setText).toHaveBeenLastCalledWith('');
    act(() => vi.runOnlyPendingTimers());
    expect(screen.getByLabelText('Message input')).toHaveValue('Help me explore my idea.');
    expect(state.setText).toHaveBeenLastCalledWith('Help me explore my idea.');
    expect(state.send).not.toHaveBeenCalled();
  });

  it('reschedules an open-event draft across a thread change and consumes it only once', () => {
    state.ready = true;
    const view = render(<AuiTaggableComposer />);
    act(() => vi.runOnlyPendingTimers());
    act(() => requestOpenCompanionChat({ draftText: 'Find an idea with me.' }));
    state.thread = 'selected-thread';
    view.rerender(<AuiTaggableComposer />);
    act(() => vi.runOnlyPendingTimers());
    expect(screen.getByLabelText('Message input')).toHaveValue('Find an idea with me.');
    fireEvent.change(screen.getByLabelText('Message input'), { target: { value: 'My revised idea' } });
    act(() => requestOpenCompanionChat());
    act(() => vi.runOnlyPendingTimers());
    expect(screen.getByLabelText('Message input')).toHaveValue('My revised idea');
    state.thread = 'another-thread';
    view.rerender(<AuiTaggableComposer />);
    act(() => vi.runOnlyPendingTimers());
    expect(screen.getByLabelText('Message input')).toHaveValue('');
    expect(state.send).not.toHaveBeenCalled();
  });
});
