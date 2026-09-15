/**
 * Two things `onNew` used to do silently.
 *
 *  1. A turn refused at the door — the concurrent-stream cap, or a thread
 *     already answering (locally or on another tab) — appended the user's
 *     message and then dropped the turn with a console.warn. The message sat
 *     there unanswered with no explanation.
 *  2. A send on a cold thread (history not yet fetched) stamped the thread
 *     as loaded when the stream ended, so its transcript never arrived.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { useAIChatRuntime, mergeColdTranscript } from "../useAIChatRuntime";
import {
  __resetThreadSessionStoreForTests,
  markRemoteTurnStarted,
} from "../threadSessionStore";
import { MAX_CONCURRENT_STREAMS } from "../threadSessionRegistry";
import type { ChatProvider, NormalizedEvent } from "../providers/types";

vi.mock("../../../context/ScopeContext", () => ({
  useScope: () => ({ scope: { workspaceId: "ws1" } }),
}));

vi.mock("../useAgentCatalog", () => ({
  useAgentCatalog: () => ({ activeAgent: { id: "agent1", name: "Agent" } }),
}));

vi.mock("../../../context/ChatPageFocusContext", () => ({
  useChatPageFocus: () => ({
    focusedTrackId: null,
    focusedViewId: null,
    focusedAppId: null,
    pageKind: null,
    focusedEntryId: null,
    visibleData: null,
    metadata: null,
    setPageFocus: vi.fn(),
    clearPageFocus: vi.fn(),
    setPageContext: vi.fn(),
    clearPageContext: vi.fn(),
  }),
}));

vi.mock("../useChatPageContextSnapshot", () => ({
  useChatPageContextSnapshot: () => () => ({ url: "/", route_path: "/" }),
}));

const history = {
  id: "t-cold",
  provider_id: "persisted",
  agent_id: "agent1",
  title: "Cold",
  archived: false,
  messages: [
    { id: "h1", role: "user", parts: [{ type: "text", text: "earlier question" }] },
    { id: "h2", role: "assistant", parts: [{ type: "text", text: "earlier answer" }] },
  ],
};

vi.mock("../../../api/aiChat", () => ({
  aiChatApi: {
    listThreads: vi.fn(async () => []),
    getThread: vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 20));
      return history;
    }),
    createThread: vi.fn(),
    cancelThread: vi.fn(async () => ({ thread_id: "t1", cancelled: true })),
  },
}));

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function* slowStream(
  _threadId: string,
  signal?: AbortSignal,
): AsyncIterable<NormalizedEvent> {
  yield { type: "text-delta", delta: "Hello" };
  for (let i = 0; i < 4; i++) {
    if (signal?.aborted) return;
    await delay(25);
  }
  yield { type: "text-delta", delta: " world" };
  yield {
    type: "message-finish",
    timing: { firstTokenMs: 1, totalMs: 2, tps: 1 },
  };
}

const mockProvider: ChatProvider = {
  id: "mock",
  label: "Mock",
  serverPersisted: false,
  capabilities: {
    reasoning: false,
    tools: false,
    attachments: false,
    vision: false,
    voice: false,
  },
  streamTurn: ({ threadId, abortSignal }) => slowStream(threadId, abortSignal),
  listAgents: async () => [],
};

const persistedProvider: ChatProvider = {
  ...mockProvider,
  id: "persisted",
  serverPersisted: true,
};

function texts(result: { current: ReturnType<typeof useAIChatRuntime> }) {
  return result.current.runtime.thread.getState().messages.map((m) =>
    (m.content as Array<{ type: string; text?: string }>)
      .map((p) => (p.type === "text" ? p.text ?? "" : ""))
      .join(""),
  );
}

async function send(
  result: { current: ReturnType<typeof useAIChatRuntime> },
  text: string,
) {
  await act(async () => {
    await result.current.runtime.thread.append({
      role: "user",
      content: [{ type: "text", text }],
    });
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  __resetThreadSessionStoreForTests();
});

describe("admission refusals are visible, and nothing is appended", () => {
  it("says the cap was hit instead of appending an unanswered message", async () => {
    const { result } = renderHook(() => useAIChatRuntime(mockProvider));

    for (let i = 0; i < MAX_CONCURRENT_STREAMS; i++) {
      await act(async () => {
        result.current.switchToNewThread();
      });
      await send(result, `turn ${i}`);
    }
    expect(result.current.streamingThreadIds).toHaveLength(MAX_CONCURRENT_STREAMS);

    await act(async () => {
      result.current.switchToNewThread();
    });
    await send(result, "one too many");

    expect(result.current.streamError).toBe(
      `Too many conversations are running (${MAX_CONCURRENT_STREAMS}). Stop one and try again.`,
    );
    expect(texts(result)).not.toContain("one too many");
    expect(result.current.streamingThreadIds).toHaveLength(MAX_CONCURRENT_STREAMS);
  });

  it("treats a turn running on another tab as busy and refuses the send", async () => {
    const { result } = renderHook(() => useAIChatRuntime(mockProvider));
    await act(async () => {
      result.current.switchToThread("t-remote");
    });
    await act(async () => {
      markRemoteTurnStarted("t-remote", "ws1", "turn-9");
    });

    // The composer sees the thread as running even though this tab holds no
    // stream for it.
    expect(result.current.isRunning).toBe(true);

    await send(result, "hello?");

    expect(result.current.streamError).toBe("This conversation is already responding.");
    // assistant-ui may leave an empty placeholder row via setMessages; the
    // user text must not land as an unanswered message.
    expect(texts(result)).not.toContain("hello?");
    expect(result.current.streamingThreadIds).toEqual([]);
  });

  it("clears the refusal when a later turn is admitted", async () => {
    const { result } = renderHook(() => useAIChatRuntime(mockProvider));
    await act(async () => {
      result.current.switchToThread("t-remote");
    });
    await act(async () => {
      markRemoteTurnStarted("t-remote", "ws1", "turn-9");
    });
    await send(result, "hello?");
    expect(result.current.streamError).toBeTruthy();

    await act(async () => {
      __resetThreadSessionStoreForTests();
      result.current.switchToNewThread();
    });
    await send(result, "fresh");
    await waitFor(() => expect(result.current.streamError).toBeNull());
  });
});

describe("a send on a cold thread does not hide its history", () => {
  it("fetches the transcript after the stream and keeps the local turn", async () => {
    const { result } = renderHook(() => useAIChatRuntime(persistedProvider));

    await act(async () => {
      result.current.switchToThread("t-cold");
    });
    // Send before the (20ms) history fetch lands; the stream outlives it.
    await send(result, "hi");

    await waitFor(() => {
      const t = texts(result);
      expect(t).toContain("earlier question");
      expect(t).toContain("earlier answer");
      expect(t).toContain("hi");
      expect(t.some((x) => x.includes("Hello world"))).toBe(true);
    });
    // History first, then this turn.
    const t = texts(result);
    expect(t.indexOf("earlier answer")).toBeLessThan(t.indexOf("hi"));
  });
});

describe("mergeColdTranscript", () => {
  const server = [
    { id: "s1", role: "user" as const, content: [{ type: "text" as const, text: "q" }] },
    { id: "s2", role: "assistant" as const, content: [{ type: "text" as const, text: "a" }] },
  ];

  it("returns the server transcript when nothing was drafted locally", () => {
    expect(mergeColdTranscript(server, [])).toBe(server);
  });

  it("drops local rows the server already has and appends the rest", () => {
    const local = [
      { id: "u-1", role: "user" as const, content: [{ type: "text" as const, text: "q" }] },
      { id: "u-2", role: "user" as const, content: [{ type: "text" as const, text: "unsaved" }] },
    ];
    // Matched rows keep the LOCAL id so ExternalStore does not fork siblings.
    expect(mergeColdTranscript(server, local).map((m) => m.id)).toEqual([
      "u-1",
      "s2",
      "u-2",
    ]);
  });

  it("preserves local ids for matching role+text pairs", () => {
    const local = [
      { id: "local-u", role: "user" as const, content: [{ type: "text" as const, text: "q" }] },
      {
        id: "local-a",
        role: "assistant" as const,
        content: [{ type: "text" as const, text: "a" }],
      },
    ];
    expect(mergeColdTranscript(server, local).map((m) => m.id)).toEqual([
      "local-u",
      "local-a",
    ]);
  });

  it("keeps local timing/finalPayload when the server row lacks them", () => {
    const local = [
      {
        id: "local-a",
        role: "assistant" as const,
        content: [{ type: "text" as const, text: "a" }],
        metadata: {
          timing: {
            streamStartTime: 0,
            totalStreamTime: 1500,
            tokenCount: 10,
            totalChunks: 1,
            toolCallCount: 0,
          },
          custom: {
            finalPayload: {
              interaction: { usage: { total_tokens: 42 } },
            },
          },
        },
      },
    ];
    const serverOnly = [
      {
        id: "s2",
        role: "assistant" as const,
        content: [{ type: "text" as const, text: "a" }],
      },
    ];
    const merged = mergeColdTranscript(serverOnly, local);
    expect(merged).toHaveLength(1);
    expect(merged[0].id).toBe("local-a");
    expect(merged[0].metadata?.timing?.totalStreamTime).toBe(1500);
    expect(
      (merged[0].metadata?.custom as { finalPayload?: unknown })?.finalPayload,
    ).toEqual({ interaction: { usage: { total_tokens: 42 } } });
  });
});
