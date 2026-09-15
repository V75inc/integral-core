import { useAuiState, useThreadViewport } from "@assistant-ui/react";
import { useLayoutEffect, useRef } from "react";

const HISTORY_LOAD_MS = 1_500;
const STREAM_PIN_MS = 15_000;

function scheduleScrollToBottom(
  scrollToBottom: (config?: { behavior?: ScrollBehavior }) => void,
) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      scrollToBottom({ behavior: "instant" });
    });
  });
}

/**
 * Ensures the transcript viewport lands at the bottom when the user switches
 * conversation tabs. assistant-ui's built-in `scrollToBottomOnThreadSwitch`
 * can run before swapped / lazy-loaded messages are measured; this component
 * re-scrolls while history finishes loading or while a background stream is
 * being brought into view.
 */
export function ThreadScrollToEndOnSwitch() {
  const mainThreadId = useAuiState((s) => s.threads.mainThreadId);
  const messageCount = useAuiState((s) => s.thread.messages.length);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const scrollToBottom = useThreadViewport((s) => s.scrollToBottom);
  const isRunningRef = useRef(isRunning);
  isRunningRef.current = isRunning;

  const followRef = useRef<{
    threadId: string;
    startedAt: number;
    pinStream: boolean;
  } | null>(null);

  useLayoutEffect(() => {
    const threadId = mainThreadId ?? "__new__";
    followRef.current = {
      threadId,
      startedAt: Date.now(),
      pinStream: isRunningRef.current,
    };
    scheduleScrollToBottom(scrollToBottom);
  }, [mainThreadId, scrollToBottom]);

  useLayoutEffect(() => {
    const follow = followRef.current;
    if (!follow) return;

    const threadId = mainThreadId ?? "__new__";
    if (follow.threadId !== threadId) return;

    const elapsed = Date.now() - follow.startedAt;
    const windowMs = follow.pinStream ? STREAM_PIN_MS : HISTORY_LOAD_MS;
    if (elapsed > windowMs) return;

    scheduleScrollToBottom(scrollToBottom);
  }, [messageCount, mainThreadId, scrollToBottom]);

  return null;
}
