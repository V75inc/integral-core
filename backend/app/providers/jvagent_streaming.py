"""jvagent SSE → normalized AI chat envelope translator.

Reads from `POST /api/agents/{agent_id}/interact?stream=true` and yields the
normalized events defined in `.planning/initiatives/ai-chat/SPEC.md` § 6.3.

Event mapping (jvchat → normalized):

  start                                                → (consumed; metadata side-channel)
  message  category=user                               → text-delta
  message  category=thought  thought_type=reasoning    → reasoning-delta (segment-grouped)
  message  category=thought  thought_type=tool_call    → tool-call (status: running)
  message  category=thought  thought_type=tool_result  → tool-call (status: complete)
  message  category=thought  thought_type=tool_progress → tool-call (post-hoc) + status (humanized)
  message  category=thought  thought_type=status       → status
  final    interaction.observability_metrics[*]        → step (usage), message-finish (timing)
  error                                                → error

Mid-stream tool-call + per-step usage events are gaps on the jvagent side
today (see SPEC § 7.3). When jvagent ships those upgrades the translator
will pick them up automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)

# Message-debug claim provenance: page-context is UI shell metadata, not a
# substrate QuerySpec. Workspace-record counts must come from the query set.
_PAGE_CONTEXT_TOOLS = frozenset({"integral_get_page_context"})
_QUERY_TOOLS = frozenset(
    {
        "integral_query",
        "integral_query_entries",
        "integral_list_apps",
        "integral_list_tracks",
        "integral_count_entries",
        "integral_search_cross_track",
        "integral_get_app",
        "integral_get_track",
        "integral_get_entry",
        "integral_list_entry_types",
        "integral_get_scope",
    }
)
_WRITE_TOOLS = frozenset(
    {
        "integral_propose_design",
        "integral_commit_batch",
        "integral_begin_batch",
        "integral_cancel_batch",
    }
)


def _claim_source(tool_name: str) -> str:
    if tool_name in _PAGE_CONTEXT_TOOLS:
        return "page_context"
    if tool_name in _QUERY_TOOLS:
        return "query"
    if tool_name in _WRITE_TOOLS or tool_name.startswith(
        ("integral_create", "integral_update", "integral_delete")
    ):
        return "write"
    return "other"


def _record_claim_tool(
    state: Dict[str, Any],
    *,
    tool_name: str,
    status: str,
    args: Any = None,
) -> None:
    claims: List[Dict[str, Any]] = state.setdefault("_claim_tools", [])
    source = _claim_source(tool_name)
    entry: Dict[str, Any] = {
        "name": tool_name,
        "source": source,
        "status": status,
    }
    if source == "query" and args is not None:
        entry["query_plan"] = args
    claims.append(entry)


def _claim_provenance(state: Dict[str, Any]) -> Dict[str, Any]:
    tools = list(state.get("_claim_tools") or [])
    return {
        "page_context_stub": (
            "UI shell metadata; not a QuerySpec or Core capability execution"
        ),
        "tools": tools,
        "page_context_tool_executed": any(
            t.get("source") == "page_context" for t in tools
        ),
        "substrate_query_executed": any(t.get("source") == "query" for t in tools),
        "query_plan": [t["query_plan"] for t in tools if t.get("query_plan")],
    }


# Map raw skill / bridge tool names → short human phrases that read
# nicely in the chat as a transient activity indicator. Anything not
# in the map falls back to a Title-Cased version of the name.
# Keep these short — they show up under the assistant message bubble
# while the model is still thinking.
_TOOL_NAME_HUMAN: Dict[str, str] = {
    "prepare_file_content": "Preparing filing proposal",
    "execute_file_content": "Applying approved filing",
    "integral_file_content": "Preparing filing proposal",
    "prepare_create_entry": "Drafting an entry",
    "execute_create_entry": "Creating the entry",
    "prepare_update_entry": "Drafting an update",
    "execute_update_entry": "Updating the entry",
    "prepare_delete_entry": "Drafting a deletion",
    "execute_delete_entry": "Deleting the entry",
    "prepare_create_track": "Drafting a track",
    "execute_create_track": "Creating the track",
    "prepare_update_track": "Drafting a track update",
    "execute_update_track": "Updating the track",
    "prepare_delete_track": "Drafting a track deletion",
    "execute_delete_track": "Deleting the track",
    "prepare_save_view": "Drafting a saved view",
    "execute_save_view": "Saving the view",
    "prepare_author_profile": "Drafting a profile",
    "execute_author_profile": "Saving the profile",
    "prepare_modify_profile": "Drafting profile changes",
    "execute_modify_profile": "Applying profile changes",
    "integral_insights__query_entries": "Searching entries",
    "integral_insights__activity_digest": "Reading recent activity",
    "integral_insights__count_entries": "Counting entries",
    "integral_workspace__list_tracks": "Looking up tracks",
    "integral_workspace__list_apps": "Looking up spaces",
    "integral_entries__list_entries": "Looking up entries",
    "integral_entries__get_entry": "Reading an entry",
    "integral_profiles__list_library_profiles": "Reading the profile library",
    "integral_profiles__get_attached_profile": "Reading the attached profile",
    "integral_identity__whoami": "Checking your identity",
}


def _parse_tool_progress(content: str) -> tuple[str, str]:
    """Split a cockpit ``tool_progress`` content line into (status, name).

    Format from jvagent's ``CockpitEngine._emit_tool_progress``:
    ``"[ok] tool_name"`` or ``"[failed] tool_name"``. Returns
    ``("", content)`` if the format doesn't match — defensive
    against future cockpit changes.
    """
    s = (content or "").strip()
    if not s.startswith("["):
        return "", s
    close = s.find("]")
    if close < 0:
        return "", s
    return s[1:close].strip(), s[close + 1 :].strip()


def _humanize_progress(name: str) -> str:
    """Map a raw tool name to a short human phrase.

    Used for the FE activity indicator. Falls back to a readable Title
    Case rendering when the name isn't in the curated map.
    """
    if not name:
        return "Working"
    direct = _TOOL_NAME_HUMAN.get(name)
    if direct:
        return direct
    # Strip any ``namespace__`` prefix (jvagent skill naming) before
    # reformatting so the indicator stays focused on the verb.
    bare = name.split("__")[-1]
    return bare.replace("_", " ").strip().capitalize() or "Working"


def _parse_sse_block(block: str) -> Optional[Dict[str, Any]]:
    """Parse a single SSE event block into a dict (data: line only)."""
    data_lines = []
    for line in block.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    raw = "\n".join(data_lines).strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("jvagent SSE: non-JSON data: %r", raw[:120])
        return None


def _extract_usage_step(metric: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull token usage out of a single observability_metrics entry.

    Accepts both legacy ``{"model_call": {...}}`` envelopes and jvagent's
    live shape ``{"event_type": "model_call", "data": {...}}``.
    """
    event_type = metric.get("event_type")
    if event_type is not None and event_type not in (
        "model_call",
        "embedding_call",
    ):
        return None
    call = metric.get("model_call") or metric.get("data") or metric
    if not isinstance(call, dict):
        return None
    usage = (
        call.get("usage")
        or call.get("token_usage")
        or call.get("metrics", {}).get("usage")
    )
    if not isinstance(usage, dict):
        return None
    in_tok = (
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("input")
        or 0
    )
    out_tok = (
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("output")
        or 0
    )
    if not (in_tok or out_tok):
        return None
    return {
        "type": "step",
        "usage": {
            "inputTokens": int(in_tok),
            "outputTokens": int(out_tok),
        },
        "modelId": call.get("model") or call.get("model_id"),
        "finishReason": call.get("finish_reason") or call.get("finishReason"),
    }


async def stream_jvagent_turn(
    *,
    base_url: str,
    agent_id: str,
    user_id: str,
    text: str,
    session_id: Optional[str],
    channel: str,
    extra_data: Optional[Dict[str, Any]] = None,
    start_time: Optional[float] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Open an SSE connection to jvagent and yield normalized events.

    Yields plain dicts whose `type` field is one of:
      text-delta, reasoning-delta, tool-call, source, status, step,
      message-finish, error.

    The caller is responsible for serializing these dicts to the wire (typ.
    SSE) — translator stays transport-agnostic so it can also feed tests.
    """
    url = f"{base_url}/api/agents/{agent_id}/interact"
    payload: Dict[str, Any] = {
        "utterance": text,
        "channel": channel,
        "user_id": user_id,
        "stream": True,
        "data": {k: v for k, v in (extra_data or {}).items() if v is not None},
    }
    if session_id:
        payload["session_id"] = session_id

    started = start_time if start_time is not None else time.monotonic()
    # Shared translator state — :func:`translate_envelope` mutates it in
    # place across calls so per-stream counters (first-token timing, chunk
    # counts, …) carry forward between SSE blocks.
    state = fresh_translator_state(started=started)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)

    try:
        async with http.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                err_body = (await response.aread()).decode("utf-8", "replace")
                yield {
                    "type": "error",
                    "code": f"jvagent_http_{response.status_code}",
                    "message": err_body[:500] or f"HTTP {response.status_code}",
                }
                return

            buffer: list[str] = []
            async for raw_line in response.aiter_lines():
                if raw_line == "":
                    block = "\n".join(buffer)
                    buffer.clear()
                    parsed = _parse_sse_block(block)
                    if not parsed:
                        continue
                    async for ev in translate_envelope(parsed, state):
                        yield ev
                else:
                    buffer.append(raw_line)
            # Flush any trailing block (some servers omit final blank line)
            if buffer:
                parsed = _parse_sse_block("\n".join(buffer))
                if parsed:
                    async for ev in translate_envelope(parsed, state):
                        yield ev

            # End-of-stream: flush any remaining tool_progress buffer
            # so the last tool call still produces a tool-call/status
            # event, even when no subsequent envelope arrived to
            # trigger the per-call flush rule.
            #
            # If the client disconnected mid-stream, the upstream
            # ``yield`` will raise (``GeneratorExit`` / cancelled task
            # / connection-closed errors depending on the FastAPI
            # transport). We swallow those because the consumer is
            # already gone — there is no value in propagating, and
            # the surrounding ``RequestError`` handler below is for
            # the jvagent connection, not the downstream consumer.
            if state.get("_progress_buffer"):
                try:
                    for ev in _emit_tool_progress(
                        state, state.get("_progress_segment_id")
                    ):
                        yield ev
                except (GeneratorExit, asyncio.CancelledError):
                    raise
                except Exception as flush_exc:  # noqa: BLE001
                    logger.debug("jvagent post-stream flush ignored: %s", flush_exc)

    except httpx.RequestError as exc:
        logger.warning("jvagent stream connection failed: %s", exc)
        yield {
            "type": "error",
            "code": "jvagent_unreachable",
            "message": f"Could not reach jvagent: {exc}",
        }
    finally:
        if owns_client:
            await http.aclose()


def fresh_translator_state(*, started: float) -> Dict[str, Any]:
    """Build a mutable counter dict for use across :func:`translate_envelope` calls.

    Callers initialize once per turn, pass the same dict to every envelope
    translation, and let the helper carry running totals (first-token timing,
    chunk counts, tool-call counts, output-token estimate). Exposed so non-HTTP
    transports (e.g. embedded jvagent under ``stream_jvagent_embed_turn``) can
    share the same translator with the HTTP path.
    """
    return {
        "first_token_ms": None,
        "text_chunk_count": 0,
        "tool_call_count": 0,
        "output_token_estimate": 0,
        "started": started,
        # Tool failures the agent may still recover from. Held here rather
        # than emitted on the spot — see the ``is_error`` branch in
        # ``translate_envelope`` for why.
        "_deferred_tool_errors": [],
        # tool_progress chunks arrive token-by-token (the cockpit
        # publishes one logical message but the bus chunks it). We
        # buffer per segment_id and flush only when the segment is
        # complete (next segment starts, OR a non-tool_progress
        # event arrives, OR the stream ends). See
        # ``_flush_tool_progress`` and the message branch.
        "_progress_buffer": "",
        "_progress_segment_id": None,
        # Id of the last category="user" message we surfaced text for. A new
        # adhoc user message (e.g. the intro greeting vs the answer) carries a
        # distinct id; stream-chunks of one logical message reuse a single id.
        # When the id changes after we've already emitted user text, we emit a
        # ``message-boundary`` so the UI renders the two as SEPARATE bubbles
        # instead of concatenating them. None until the first user text.
        "_last_user_msg_id": None,
        "_claim_tools": [],
        # Set of segment_ids we've already emitted a real
        # ``tool-call`` event for via the SPEC §7.3 structured
        # envelope path. When tool_progress flushes for a segment in
        # this set, we skip the synthetic tool-call event (the real
        # one already carried args + result) but STILL emit the
        # humanized ``status`` event for the activity strip — that
        # piece is purely UX and is still useful even when the
        # structured envelopes are present.
        "_real_tool_segments": set(),
    }


def _emit_tool_progress(
    state: Dict[str, Any], segment_id: Optional[str]
) -> List[Dict[str, Any]]:
    """Drain accumulated content for the active tool_progress segment.

    Returns the events the caller should yield.

    Two emissions in the general case:
      1. A synthetic ``tool-call`` event — ONLY when no real
         structured envelope was seen for this segment. Once
         jvagent ships SPEC §7.3, real ``tool_call`` /
         ``tool_result`` envelopes carry the actual args + results
         and we don't want to double-emit a payload-less synthetic
         event. The state's ``_real_tool_segments`` set tracks
         which segments already had a real envelope; we skip the
         synthetic for those.
      2. A humanized ``status`` event — ALWAYS emitted (when the
         buffer is non-empty). This is what fuels the activity
         strip's "Filing your content…" indicator. The status text
         is purely UX and remains useful even when the structured
         envelopes are present.

    Returns an empty list when the buffer is empty. Always clears
    the buffer state, regardless of which events were yielded.
    """
    buf = (state.get("_progress_buffer") or "").strip()
    seg = segment_id
    state["_progress_buffer"] = ""
    state["_progress_segment_id"] = None
    if not buf:
        return []
    progress_status, progress_name = _parse_tool_progress(buf)

    events: List[Dict[str, Any]] = []
    real_segments = state.get("_real_tool_segments") or set()
    seg_already_seen_as_real = seg is not None and seg in real_segments
    if not seg_already_seen_as_real:
        state["tool_call_count"] += 1
        events.append(
            {
                "type": "tool-call",
                "toolCallId": (seg or f"progress-{state['tool_call_count']}"),
                "name": progress_name or "tool",
                "status": ("error" if progress_status == "failed" else "complete"),
                # No ``result`` — the tool_progress text is a
                # human-readable summary, not the actual tool
                # output. The structured tool_result envelope
                # (SPEC §7.3) carries that.
            }
        )
    events.append({"type": "status", "text": _humanize_progress(progress_name)})
    return events


async def translate_envelope(
    parsed: Dict[str, Any],
    state: Dict[str, Any],
) -> AsyncIterator[Dict[str, Any]]:
    """Translate one jvchat envelope into zero or more normalized events.

    Public counterpart of the prior ``_translate`` helper. ``state`` is a
    mutable dict the caller owns; this function updates it in place so the
    outer loop can carry running counters between invocations. Use
    :func:`fresh_translator_state` to build the initial dict.
    """
    kind = parsed.get("type")

    # Flush rule for buffered tool_progress (accumulates per segment_id):
    #   - any non-message event (start / final / error) — flush
    #   - any message event that is NOT a tool_progress with the SAME
    #     segment_id — flush before processing the new event
    # Without this the first non-tool_progress event after a tool
    # finishes never gets the buffered progress emitted.
    if state.get("_progress_buffer"):
        should_flush = True
        if kind == "message":
            msg = parsed.get("message") or {}
            if (
                msg.get("category") == "thought"
                and msg.get("thought_type") == "tool_progress"
                and msg.get("segment_id") == state.get("_progress_segment_id")
            ):
                should_flush = False
        if should_flush:
            for ev in _emit_tool_progress(state, state.get("_progress_segment_id")):
                yield ev

    if kind == "start":
        # Side-channel meta event (consumed by the proxy, not forwarded to
        # the browser) so the proxy can capture jvagent's session_id and
        # persist it on the thread for future-turn resume.
        sid = parsed.get("session_id")
        iid = parsed.get("interaction_id")
        uid = parsed.get("user_id")
        if sid or iid or uid:
            yield {
                "type": "_meta",
                "provider_session_id": sid,
                "provider_interaction_id": iid,
                "provider_user_id": uid,
            }
        if iid:
            state["_interaction_id"] = str(iid)
        return

    if kind == "message":
        message = parsed.get("message") or {}
        category = message.get("category")
        content = message.get("content") or ""
        thought_type = message.get("thought_type")
        segment_id = message.get("segment_id")

        if category == "user":
            # End-of-stream / empty frames are not user text. jvagent emits
            # message_type=final (often under a fresh Object id from
            # finalize_interaction) with empty content; treating that as a
            # new user message splits a second bubble.
            if (message.get("message_type") or "") == "final" or not content:
                return
            # Same interaction, same prose again (fresh-session Hello on a
            # rematerialized Interaction / second bus) is not a new bubble.
            fingerprint = (
                str(
                    parsed.get("interaction_id")
                    or message.get("interaction_id")
                    or state.get("_interaction_id")
                    or ""
                ),
                content.strip(),
            )
            last_fp = state.get("_last_user_fingerprint")
            if last_fp and (
                fingerprint == last_fp
                or (fingerprint[1] and fingerprint[1] == last_fp[1])
            ):
                return
            state["_last_user_fingerprint"] = fingerprint
            # Bubble boundary: a distinct user-message id after we've already
            # surfaced user text means a NEW logical message (the orchestrator
            # publishes the intro greeting and the answer as separate adhoc
            # messages; the loop's reply is its own id). Emit a boundary so the
            # UI starts a fresh bubble rather than appending to the prior one.
            # Stream-chunks of one message share an id → no boundary between
            # them. Messages without an id fall back to the old single-bubble
            # behavior (no boundary).
            msg_id = message.get("id")
            last_id = state.get("_last_user_msg_id")
            if (
                msg_id
                and last_id is not None
                and msg_id != last_id
                and state["text_chunk_count"] > 0
            ):
                yield {"type": "message-boundary"}
            if msg_id:
                state["_last_user_msg_id"] = msg_id
            if state["first_token_ms"] is None:
                state["first_token_ms"] = (time.monotonic() - state["started"]) * 1000.0
            state["text_chunk_count"] += 1
            state["output_token_estimate"] += max(1, len(content) // 4)
            yield {"type": "text-delta", "delta": content}
            return

        if category == "thought":
            if thought_type == "reasoning":
                yield {
                    "type": "reasoning-delta",
                    "delta": content,
                    "segmentId": segment_id or "default",
                }
                return
            if thought_type in ("tool_call", "tool_result"):
                # SPEC §7.3 structured envelopes. jvagent ships the
                # rich payload (tool_call_id, tool_name, tool_args /
                # tool_result, is_error) inside ``message.metadata``
                # — that's the only field of ``ResponseMessage`` that
                # the bus surfaces in the wire envelope alongside the
                # standard fields. Read from there first, then fall
                # back to top-level fields (forward-compat in case
                # the upstream emission path changes).
                meta = message.get("metadata") or {}
                state["tool_call_count"] += 1
                tool_call_id = (
                    meta.get("tool_call_id")
                    or message.get("tool_call_id")
                    or segment_id
                    or message.get("id")
                    or "tool"
                )
                # Register this segment so the post-hoc tool_progress
                # synthesis later in the stream knows a real envelope
                # already covered it — and skips the synthetic
                # tool-call to avoid double-emit.
                if segment_id:
                    state.setdefault("_real_tool_segments", set()).add(segment_id)
                tool_name = meta.get("tool_name") or message.get("tool_name") or "tool"
                payload: Dict[str, Any] = {
                    "type": "tool-call",
                    "toolCallId": tool_call_id,
                    "name": tool_name,
                    "status": (
                        ("error" if meta.get("is_error") else "complete")
                        if thought_type == "tool_result"
                        else "running"
                    ),
                }
                if thought_type == "tool_call":
                    payload["args"] = (
                        meta.get("tool_args")
                        or message.get("tool_args")
                        or {"_text": content}
                    )
                else:
                    # Real tool result, not the cockpit's post-hoc
                    # human-readable summary. This is the actual JSON
                    # the tool returned to the model.
                    payload["result"] = (
                        meta.get("tool_result")
                        if "tool_result" in meta
                        else (
                            message.get("tool_result")
                            if "tool_result" in message
                            else content
                        )
                    )
                yield payload
                if thought_type == "tool_result":
                    _record_claim_tool(
                        state,
                        tool_name=str(tool_name),
                        status=str(payload.get("status") or "complete"),
                        args=meta.get("tool_args") or message.get("tool_args"),
                    )
                # A failed tool result is HELD, not emitted, until we know
                # whether the turn recovered from it.
                #
                # This used to yield a top-level error immediately, so the
                # frontend's MessageError banner fired on any tool failure —
                # including ones the orchestrator handled and moved past. Asked
                # to "find all bugs related to Safari", the resident tried
                # ``integral_query``, fell back, and answered correctly ("No
                # bugs related to Safari were found… want me to search across
                # all tracks?") — and the user still got a red "Action
                # 'integral_query' could not be completed." under a complete,
                # accurate reply. A turn that succeeded reads as a turn that
                # failed, which trains people to distrust the banner exactly
                # when it is telling the truth.
                #
                # The failure is NOT hidden: ``payload["status"] = "error"``
                # above still marks the tool call, so it stays visible in the
                # tech-detail disclosure. Only the turn-level banner waits for
                # the verdict, emitted at ``final`` when no answer materialized.
                if thought_type == "tool_result" and meta.get("is_error"):
                    state.setdefault("_deferred_tool_errors", []).append(
                        {
                            "type": "error",
                            "code": "tool_failed",
                            "message": f"Action '{tool_name}' could not be completed.",
                        }
                    )
                return
            if thought_type == "status":
                yield {"type": "status", "text": content}
                return
            if thought_type == "tool_progress":
                # The cockpit emits this AFTER each tool call resolves.
                # Logical content is ``"[ok] prepare_file_content"`` or
                # ``"[failed] integral_insights__count_entries"`` — but
                # the response_bus chunks the publish into many small
                # SSE messages (``"["``, ``"ok"``, ``"]"``, ``" "``,
                # ``"integral"``, ``"_filing"``, …) all sharing one
                # ``segment_id``. Treating each chunk as a complete
                # tool name produces useless gibberish in the activity
                # strip ("Working", "Ok", "Filing", "Working", …), so
                # we accumulate per segment_id and only emit when the
                # buffer is flushed (different segment, non-progress
                # event, or end of stream — see the flush rule at the
                # top of ``translate_envelope`` and the streamer's
                # final flush).
                state["_progress_buffer"] = (
                    state.get("_progress_buffer") or ""
                ) + content
                state["_progress_segment_id"] = (
                    segment_id or message.get("id") or "default"
                )
                return
            # Unknown thought_type — degrade gracefully
            yield {"type": "status", "text": content}
            return
        return

    if kind == "final":
        interaction = parsed.get("interaction") or {}
        # Forward the authoritative final payload for the debug view. jvchat
        # stores the entire `final` chunk as `debugData` and shows
        # `interaction.response` as "Message Content" + the whole chunk as
        # "Full JSON Response". Mirror that: `content` = the settled answer
        # text, `payload` = the full final chunk.
        final_response = interaction.get("response")
        content = final_response if isinstance(final_response, str) else None
        if content:
            # The FE renders this settled answer in preference to the streamed
            # tokens (Thread.tsx: messageContent = finalContent ?? assembledText),
            # so resolve raw node ids → human names here too. Best-effort.
            try:
                from app.services.id_resolver import humanize_ids

                content = await humanize_ids(content)
            except Exception:  # noqa: BLE001
                logger.debug("jvagent_streaming.humanize_failed", exc_info=True)
        # Verdict on any tool failures held during the turn: an answer means
        # the agent recovered, so the banner would contradict what the user is
        # reading. No answer means the failure IS the outcome and has to
        # surface — otherwise the turn ends silently with nothing to show.
        deferred = state.pop("_deferred_tool_errors", []) or []
        if deferred and not content:
            for err in deferred:
                yield err
        yield {
            "type": "final-content",
            "content": content,
            "payload": {
                **parsed,
                "claim_provenance": _claim_provenance(state),
            },
        }
        metrics = interaction.get("observability_metrics") or []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            step = _extract_usage_step(metric)
            if step:
                yield step

        total_ms = (time.monotonic() - state["started"]) * 1000.0
        timing: Dict[str, Any] = {"totalMs": total_ms}
        if state["first_token_ms"] is not None:
            timing["firstTokenMs"] = state["first_token_ms"]
        if state["output_token_estimate"] and total_ms > 0:
            timing["tps"] = (state["output_token_estimate"] / total_ms) * 1000.0
        yield {"type": "message-finish", "timing": timing}
        return

    if kind == "error":
        yield {
            "type": "error",
            "code": "jvagent_error",
            "message": parsed.get("message") or "Unknown error",
        }
        return
