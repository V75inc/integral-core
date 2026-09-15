/**
 * The remembered "last active thread" is workspace-scoped.
 *
 * It was a bare thread id, so after switching workspaces the runtime's
 * mount-time restore re-selected a conversation from the previous one.
 */
import { describe, it, expect, beforeEach } from 'vitest';

import {
  peekLastActiveChatThreadId,
  rememberActiveChatThreadId,
} from '../chatHandoff';

beforeEach(() => {
  sessionStorage.clear();
});

describe('rememberActiveChatThreadId / peekLastActiveChatThreadId', () => {
  it('returns the thread for the workspace it was remembered in', () => {
    rememberActiveChatThreadId('n.ChatThread.1', 'ws1');
    expect(peekLastActiveChatThreadId('ws1')).toBe('n.ChatThread.1');
  });

  it('ignores a thread remembered in another workspace', () => {
    rememberActiveChatThreadId('n.ChatThread.1', 'ws1');
    expect(peekLastActiveChatThreadId('ws2')).toBeNull();
  });

  it('stays readable by callers that do not know the workspace', () => {
    // The dock's handoff listener and the undo stash read without one.
    rememberActiveChatThreadId('n.ChatThread.1', 'ws1');
    expect(peekLastActiveChatThreadId()).toBe('n.ChatThread.1');
    rememberActiveChatThreadId('n.ChatThread.2');
    expect(peekLastActiveChatThreadId('ws9')).toBe('n.ChatThread.2');
  });

  it('still drops local (unsaved) thread ids', () => {
    rememberActiveChatThreadId('n.ChatThread.1', 'ws1');
    rememberActiveChatThreadId('local-xyz', 'ws1');
    expect(peekLastActiveChatThreadId('ws1')).toBeNull();
  });

  it('tolerates a pre-keyed slot holding a bare id', () => {
    sessionStorage.setItem('integral:ai-chat-last-thread', 'n.ChatThread.old');
    expect(peekLastActiveChatThreadId('ws1')).toBe('n.ChatThread.old');
  });
});
