"""Shared SSE turn loop for AI chat endpoints (user + agent-initiated)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, AsyncGenerator, AsyncIterator, Dict, List, Optional, Tuple, cast

from fastapi import Request

from app.services import chat_turn_registry
from app.services.chat_providers import ChatBackendProvider, ChatTurnContext
from app.services.chat_thread_events import notify_thread_stream_update
from app.services.chat_turn_registry import InFlightTurn

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_bytes(event: str, data: Dict[str, Any]) -> bytes:
    """Encode a named SSE event frame with JSON-serialized payload."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")


class _DeltaHumanizer:
    """Resolve raw node ids → names inside the streamed assistant text.

    Per-token deltas can split an id across chunks, so a delta is never
    humanized alone. Instead we buffer and only release text up to the last
    whitespace — a whole id (which contains no whitespace) is always complete
    before it leaves the buffer, so ``humanize_ids`` sees the full token. The
    raw deltas still flow to ``turn_events`` for persistence (which humanizes
    independently); this only cleans the LIVE wire. Best-effort and bounded to
    one trailing partial word of latency.
    """

    def __init__(self) -> None:
        self._buf = ""

    async def _humanize(self, text: str) -> str:
        if not text:
            return text
        try:
            from app.services.id_resolver import humanize_ids

            return await humanize_ids(text)
        except Exception:  # noqa: BLE001
            logger.debug("chat_streaming.humanize_failed", exc_info=True)
            return text

    async def feed(self, delta: str) -> Optional[str]:
        """Buffer ``delta``; return humanized complete words (or None)."""
        self._buf += delta or ""
        cut = max(self._buf.rfind(" "), self._buf.rfind("\n"), self._buf.rfind("\t"))
        if cut < 0:
            return None
        ready, self._buf = self._buf[: cut + 1], self._buf[cut + 1 :]
        return await self._humanize(ready)

    async def flush(self) -> Optional[str]:
        """Humanize + return any buffered remainder (end of message/stream)."""
        if not self._buf:
            return None
        ready, self._buf = self._buf, ""
        return await self._humanize(ready)


# Wire codes for a turn that died with an exception. The raw exception text
# never leaves the process — it is logged with the stack; the browser gets a
# stable code plus a sentence a person can act on.
_ERROR_MESSAGES: Dict[str, str] = {
    "model_key_required": (
        "No model API key is configured for this workspace. "
        "Add one in Settings and try again."
    ),
    "walker_failed": (
        "The assistant hit an internal error before it could finish. "
        "Please try again."
    ),
    "internal_error": "Something went wrong on our side. Please try again.",
}


def classify_turn_exception(exc: BaseException) -> Tuple[str, str]:
    """Map an exception raised mid-stream to ``(code, user_facing_message)``."""
    model_key_exc: Optional[type] = None
    try:
        from app.services.model_credential_resolver import (
            ModelKeyRequiredError as _ModelKeyRequiredError,
        )

        model_key_exc = _ModelKeyRequiredError
    except Exception:  # noqa: BLE001 — resolver is optional at import time
        model_key_exc = None
    if model_key_exc is not None and isinstance(exc, model_key_exc):
        code = "model_key_required"
    elif type(exc).__module__.startswith("jvagent"):
        code = "walker_failed"
    else:
        code = "internal_error"
    return code, _ERROR_MESSAGES[code]


def _register_cancel_hook(turn_handle: InFlightTurn, thread_id: str) -> None:
    from app.providers import jvagent_embed

    def _cancel() -> None:
        jvagent_embed.cancel_interact(thread_id=thread_id)

    turn_handle.register_cancel_hook(_cancel)


async def _handle_meta_event(
    ev: Dict[str, Any],
    *,
    thread: Any,
    provider_session_seen: Optional[str],
    interact_payload: Dict[str, Any],
    persist_session,
) -> Optional[str]:
    if ev.get("clear_provider_session"):
        try:
            from app.services.chat_threads import clear_provider_session

            await clear_provider_session(thread)
        except Exception:
            logger.exception("Failed to clear orphaned provider_session_id")
        interact_payload["provider_session_id"] = None
        return None

    sid = ev.get("provider_session_id")
    if sid and sid != provider_session_seen:
        provider_session_seen = sid
        try:
            await persist_session(thread, provider_session_seen)
        except Exception:
            logger.exception("Failed to persist provider_session_id early")
    interact_payload["provider_session_id"] = sid
    iid = ev.get("provider_interaction_id")
    if iid:
        interact_payload["provider_interaction_id"] = iid
    return provider_session_seen


async def _checkpoint_on_boundary(
    turn_events: List[Dict[str, Any]],
    *,
    thread: Any,
    checkpoint_draft_index: int,
    interact_payload: Dict[str, Any],
    drafts_from_events,
    persist_assistant_drafts,
    thread_id: str,
) -> int:
    try:
        drafts = drafts_from_events(turn_events)
        end = max(0, len(drafts) - 1)
        return await persist_assistant_drafts(
            thread,
            turn_events,
            start_index=checkpoint_draft_index,
            end_index=end,
            interact_payload=interact_payload,
        )
    except Exception:
        logger.exception("Failed checkpoint persist for thread=%s", thread_id)
        return checkpoint_draft_index


async def generate_chat_turn_sse(
    *,
    request: Request,
    thread: Any,
    user_id: str,
    provider: ChatBackendProvider,
    turn_handle: InFlightTurn,
    turn_ctx: ChatTurnContext,
    interact_payload: Dict[str, Any],
    drafts_from_events,
    persist_assistant_drafts,
    persist_provider_session_if_needed,
    notify_extra: Optional[Dict[str, Any]] = None,
    error_log_label: str = "AI chat stream",
    on_terminal=None,
    on_event=None,
) -> AsyncIterator[bytes]:
    """Run one provider stream, yielding SSE bytes until completion or cancel."""
    turn_events: List[Dict[str, Any]] = []
    checkpoint_draft_index = 0
    provider_session_seen: Optional[str] = thread.provider_session_id

    _register_cancel_hook(turn_handle, thread.id)
    humanizer = _DeltaHumanizer()

    persisted = False
    terminal_status = "cancelled"
    terminal_error: Optional[Dict[str, Any]] = None

    async def _flush_drafts() -> None:
        """Persist everything accumulated since the last checkpoint. Idempotent.

        Called on the normal end of stream AND from ``finally`` when the turn
        was interrupted, because tool calls have already had their real-world
        effect by the time we get here — a staged token is minted, a write is
        queued — and dropping the message that carries their results orphans
        those effects from the conversation that produced them.
        """
        nonlocal persisted
        if persisted:
            return
        persisted = True
        await persist_provider_session_if_needed(thread, provider_session_seen)
        drafts = drafts_from_events(turn_events)
        await persist_assistant_drafts(
            thread,
            turn_events,
            start_index=checkpoint_draft_index,
            end_index=len(drafts),
            interact_payload=interact_payload,
        )

    async def _flush_drafts_logged() -> None:
        try:
            await _flush_drafts()
        except Exception:
            logger.exception(
                "Failed to persist interrupted turn for thread=%s", thread.id
            )

    yield sse_bytes(
        "interact-context",
        {"type": "interact-context", "payload": interact_payload},
    )

    # ``completed`` flips only when the provider iterator was drained to its
    # natural end. Anything else — client disconnect, /cancel, an exception,
    # the ASGI server cancelling us at a yield — leaves it False, and the
    # harness turn behind the iterator must then be cancelled explicitly:
    # jvagent's walker runs as its own task and keeps calling the model and
    # dispatching tools after the stream that fed it is gone.
    completed = False
    cancelled = False
    try:
        # ``aclosing`` runs the provider generator's ``finally`` blocks (the
        # scope/focus ContextVar resets in jvagent_provider) in THIS task's
        # context when the loop exits early. Left to the event loop's
        # asyncgen finalizer they run in a different Context and
        # ``ContextVar.reset(token)`` raises.
        async with contextlib.aclosing(
            cast(
                AsyncGenerator[Dict[str, Any], None],
                provider.stream_turn(turn_ctx),
            )
        ) as stream:
            try:
                async for ev in stream:
                    if (
                        await request.is_disconnected()
                        or turn_handle.cancel_event.is_set()
                    ):
                        cancelled = True
                        break

                    if ev.get("type") == "_meta":
                        provider_session_seen = await _handle_meta_event(
                            ev,
                            thread=thread,
                            provider_session_seen=provider_session_seen,
                            interact_payload=interact_payload,
                            persist_session=persist_provider_session_if_needed,
                        )
                        continue

                    turn_events.append(ev)  # raw events drive persistence

                    if ev.get("type") == "error":
                        terminal_status = "failed"
                        terminal_error = {
                            "code": str(ev.get("code") or "provider_error"),
                            "message": str(ev.get("message") or "Provider failed"),
                        }

                    # Execution receipts are deliberately written only for
                    # model/tool boundaries, never for token deltas. A failed
                    # observability write must not turn a successful provider
                    # response into a chat failure.
                    if on_event is not None:
                        try:
                            await on_event(ev, ordinal=len(turn_events))
                        except Exception:
                            logger.exception(
                                "Failed to persist run event for thread=%s", thread.id
                            )

                    # Stream assistant text through the id-humanizing buffer
                    # so the live bubble never shows a raw n.<Type>.<hex> id.
                    # Raw deltas still went to turn_events above.
                    if ev.get("type") == "text-delta":
                        ready = await humanizer.feed(ev.get("delta") or "")
                        if ready:
                            yield sse_bytes(
                                "text-delta", {"type": "text-delta", "delta": ready}
                            )
                        continue

                    # Any non-text event marks a boundary for buffered text —
                    # flush the humanized remainder before the event so
                    # ordering is preserved.
                    pending = await humanizer.flush()
                    if pending:
                        yield sse_bytes(
                            "text-delta", {"type": "text-delta", "delta": pending}
                        )

                    if ev.get("type") == "message-boundary":
                        checkpoint_draft_index = await _checkpoint_on_boundary(
                            turn_events,
                            thread=thread,
                            checkpoint_draft_index=checkpoint_draft_index,
                            interact_payload=interact_payload,
                            drafts_from_events=drafts_from_events,
                            persist_assistant_drafts=persist_assistant_drafts,
                            thread_id=thread.id,
                        )

                    yield sse_bytes(ev["type"], ev)
                else:
                    completed = True
            finally:
                # Before ``aclosing`` closes the provider generator: the
                # embed transport unregisters its walker task in its own
                # ``finally``, so the cancel hook (embed.cancel_interact
                # keyed by thread_id) only finds the walker while the
                # generator is still open.
                if not completed:
                    turn_handle.cancel()

        # Flush any trailing buffered text at end of stream.
        pending = await humanizer.flush()
        if pending:
            yield sse_bytes("text-delta", {"type": "text-delta", "delta": pending})

        try:
            await _flush_drafts()
        except Exception:
            logger.exception(
                "Failed to persist assistant message for thread=%s",
                thread.id,
            )

        if cancelled:
            terminal_status = "cancelled"
            yield sse_bytes(
                "status",
                {"type": "status", "status": "cancelled", "text": "Turn cancelled."},
            )

        elif completed and terminal_status != "failed":
            terminal_status = "succeeded"

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("%s failed for thread=%s", error_log_label, thread.id)
        code, message = classify_turn_exception(exc)
        error_event = {"type": "error", "code": code, "message": message}
        terminal_status = "failed"
        terminal_error = {"code": code, "message": message}
        # Persist the failure as part of the assistant turn so the transcript
        # shows WHY there is no answer after a reload, instead of a user
        # message with nothing under it.
        turn_events.append(error_event)
        await _flush_drafts_logged()
        yield sse_bytes("error", error_event)
    finally:
        # The turn can end without reaching the persist above: the ASGI server
        # cancels this generator at its `yield` when the client disconnects
        # (navigation, tab close, a routine switching threads), so the
        # CancelledError skips straight here. Everything since the last
        # message-boundary checkpoint would be lost — observed in the wild as a
        # staged change with no card, because commit_batch had run but the
        # message carrying its token never landed.
        #
        # Run the flush as its own task and shield it: awaiting directly during
        # cancellation re-raises immediately. If the shield is cancelled the
        # inner task still completes, and it logs its own failures.
        if not persisted and turn_events:
            flush_task = asyncio.create_task(_flush_drafts_logged())
            try:
                await asyncio.shield(flush_task)
            except asyncio.CancelledError:
                # Swallowed deliberately: the original cancellation keeps
                # propagating once this block finishes, and the shielded task
                # runs to completion on its own.
                pass
            except Exception:
                logger.exception(
                    "Unexpected error flushing interrupted turn for thread=%s",
                    thread.id,
                )
        await chat_turn_registry.release_turn(thread.id)
        if on_terminal is not None:
            try:
                await on_terminal(terminal_status, terminal_error)
            except Exception:
                logger.exception(
                    "Failed to persist terminal run for thread=%s", thread.id
                )
        await notify_thread_stream_update(
            user_id,
            thread.id,
            status="completed",
            workspace_id=getattr(thread, "workspace_id", None) or None,
            turn_id=turn_handle.turn_id,
            extra=notify_extra,
        )
