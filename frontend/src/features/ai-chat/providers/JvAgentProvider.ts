import { aiChatApi } from "../../../api/aiChat";
import { getActiveScopeHeader } from "../../../api/client";
import { getAccessToken, refreshAccessToken } from "../../../api/session";
import { getApiBaseURL } from "../../../config";
import type { ChatProvider, NormalizedEvent, TurnContext } from "./types";

const SCOPE_KEY = "integral.scope";

/** Read the persisted workspace scope and serialise it as the header
 *  value the backend dispatcher expects. Returns null when the slot is
 *  empty / corrupt — caller omits the header in that case. */
function readScopeHeader(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(SCOPE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // W6 canonical shape: { workspaceId: string }
    if (parsed?.workspaceId && typeof parsed.workspaceId === "string") {
      return `ws:${parsed.workspaceId}`;
    }
  } catch {
    /* corrupt slot — fall through */
  }
  return null;
}

/**
 * Parses a server-sent-events payload incrementally and yields parsed JSON
 * objects (one per event block). Stops cleanly when the stream ends or the
 * abort signal fires.
 */
async function* readSseEvents(
  response: Response,
  signal: AbortSignal,
): AsyncIterable<{ event: string; data: unknown }> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (signal.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sepIdx: number;
      while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const parsed = parseSseBlock(block);
        if (parsed) yield parsed;
      }
    }
    if (buffer.trim().length > 0) {
      const parsed = parseSseBlock(buffer);
      if (parsed) yield parsed;
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseBlock(block: string): { event: string; data: unknown } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}

export const JvAgentProvider: ChatProvider = {
  id: "jvagent",
  label: "jvagent",
  serverPersisted: true,
  capabilities: {
    reasoning: true,
    tools: true,
    attachments: true,
    vision: true,
    voice: false,
  },

  async listAgents() {
    return aiChatApi.listAgents("jvagent");
  },

  async *streamTurn(ctx: TurnContext): AsyncIterable<NormalizedEvent> {
    if (!ctx.threadId) {
      yield {
        type: "error",
        code: "missing_thread_id",
        message: "No active chat thread.",
      };
      return;
    }

    // Forward the active workspace scope so jvagent's tool calls are constrained
    // to the workspace the user is looking at. Prefer the LIVE in-memory header
    // (getActiveScopeHeader, kept in sync by ScopeContext from /users/me/scope) —
    // it is correct on a fresh boot. The localStorage slot is only written on an
    // explicit switch and is null until then, which silently fell the agent back
    // to the personal workspace even when the user was in an org workspace.
    const scopeHeader = getActiveScopeHeader() ?? readScopeHeader();

    // This stream is a raw fetch (SSE), so it does NOT pass through the axios
    // client's 401 → refresh → retry interceptor the rest of the app relies on.
    // It also used to read the token straight out of localStorage. The result
    // was that an access token expiring mid-session surfaced as a chat error
    // with no refresh attempt, while every other surface silently recovered —
    // the user just saw the assistant stop working until they reloaded.
    //
    // Mirror the interceptor's contract here: try once, and on a 401 exchange
    // the refresh token (single-flight, shared with the interceptor via
    // api/session) and replay the request exactly once. `refreshAccessToken`
    // owns the failure semantics — 4xx tears the session down and emits
    // `integral:logout`, 5xx leaves tokens alone for a later retry — so a null
    // return here means "genuinely unauthenticated", not "try again".
    const doFetch = (bearer: string | null): Promise<Response> =>
      fetch(
        `${getApiBaseURL()}/chat/threads/${encodeURIComponent(ctx.threadId!)}/messages`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
            ...(scopeHeader ? { "X-Integral-Scope": scopeHeader } : {}),
          },
          body: JSON.stringify({
            text: ctx.userMessageText,
            agent_id: ctx.agentId ?? null,
            entity_refs: ctx.entityRefs?.length ? ctx.entityRefs : undefined,
            images: ctx.images?.length ? ctx.images : undefined,
            attachment_ids: ctx.attachmentIds?.length ? ctx.attachmentIds : undefined,
            focused_track_id: ctx.focusedTrackId ?? undefined,
            focused_view_id: ctx.focusedViewId ?? undefined,
            focused_space_id: ctx.focusedAppId ?? undefined,
            page_context: ctx.pageContext ?? undefined,
          }),
          signal: ctx.abortSignal,
        },
      );

    let response: Response;
    try {
      response = await doFetch(getAccessToken());
      if (response.status === 401) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
          response = await doFetch(refreshed);
        }
      }
    } catch (err) {
      if ((err as DOMException)?.name === "AbortError") return;
      yield {
        type: "error",
        code: "network",
        message: err instanceof Error ? err.message : String(err),
      };
      return;
    }

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      // 409 is the one status with a meaning worth saying out loud: the
      // backend admits one in-flight turn per thread (I-CHAT-PAR-01), so
      // this is "it is already answering", not a failure. The raw envelope
      // rendered as `http_409` plus a JSON blob, which reads like a crash.
      if (response.status === 409) {
        // Two different conflicts arrive as 409 and want different things from
        // the user: this thread is busy (wait or stop THIS one), or too many
        // of their conversations are running at once (stop ANY one). The
        // backend distinguishes them in `details.reason`; telling someone to
        // stop a thread that is not the problem sends them the wrong way.
        let reason = "";
        let serverMessage = "";
        try {
          const parsed = JSON.parse(body) as {
            message?: string;
            details?: { reason?: string };
          };
          reason = parsed.details?.reason ?? "";
          serverMessage = parsed.message ?? "";
        } catch {
          /* non-JSON envelope — fall through to the thread-busy default */
        }
        yield {
          type: "error",
          code: reason === "user_turn_limit" ? "user_turn_limit" : "turn_in_flight",
          message:
            reason === "user_turn_limit" && serverMessage
              ? serverMessage
              : "This conversation is already responding. Wait for it to finish, or stop it first.",
        };
        return;
      }
      // Every other failure: say the server's own sentence when the
      // envelope carries one, otherwise a human line with the status. The raw
      // JSON envelope used to be rendered verbatim as the assistant's reply.
      let serverMessage = "";
      try {
        const parsed = JSON.parse(body) as { message?: unknown };
        if (typeof parsed?.message === "string") serverMessage = parsed.message.trim();
      } catch {
        /* non-JSON body — use the generic sentence */
      }
      yield {
        type: "error",
        code: `http_${response.status}`,
        message:
          serverMessage ||
          `The assistant could not process this request (HTTP ${response.status}). Please try again.`,
      };
      return;
    }

    try {
      for await (const ev of readSseEvents(response, ctx.abortSignal)) {
        if (ctx.abortSignal.aborted) break;
        const payload = ev.data;
        if (payload && typeof payload === "object") {
          yield payload as NormalizedEvent;
        }
      }
    } catch (err) {
      if ((err as DOMException)?.name === "AbortError") return;
      yield {
        type: "error",
        code: "stream_read_failed",
        message: err instanceof Error ? err.message : String(err),
      };
    }
  },
};
