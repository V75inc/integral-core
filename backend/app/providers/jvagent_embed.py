"""Embed-mode jvagent chat provider.

Counterpart of :mod:`app.providers.jvagent_streaming` for the case where
jvagent is loaded inside this process via :func:`jvagent.embed.bootstrap`
rather than reached over HTTP.

Yields the same normalized envelope ai_chat consumes (text-delta /
reasoning-delta / tool-call / source / status / step / message-finish /
error / `_meta` side channel) so the caller in ``app/api/ai_chat.py`` can
branch transport without changing its draft accumulator or persistence
path.

Implementation notes:

* Calls :func:`jvagent.embed.interact_stream` to get jvchat envelope dicts
  (``start``/``message``/``final``/``error``) directly — no HTTP, no SSE
  parsing — and pipes them through the same
  :func:`app.providers.jvagent_streaming.translate_envelope` used by the
  HTTP path. Both transports therefore produce identical normalized
  events.
* Surfaces an additional ``_meta`` event after the jvchat ``start`` so
  ``ai_chat.send_message`` can capture jvagent's session id and persist
  it on the thread for resume on the next turn (matches the HTTP
  translator's behavior).
* On an orphaned provider session (``interaction_not_created`` after a
  harness Memory rebuild, etc.), clears the persisted session once and
  retries the turn with a fresh session — clients never need a manual
  "new thread".
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from app.providers.jvagent_streaming import (
    _emit_tool_progress,
    fresh_translator_state,
    translate_envelope,
)

logger = logging.getLogger(__name__)

# User-facing text for terminal failures. The raw exception (``TypeName:
# detail``) is logged server-side only — it can carry internal paths,
# provider payloads, and other users' data, and the browser cannot act on
# it anyway. Codes not listed here (``access_denied``, ``agent_not_found``)
# already carry a message written for the user and pass through unchanged.
USER_FACING_ERROR_MESSAGES: Dict[str, str] = {
    "walker_failed": (
        "The assistant hit an internal error before it could finish. "
        "Please try again."
    ),
    "embed_interact_failed": (
        "The assistant could not be reached for this turn. Please try again."
    ),
    "interaction_not_created": (
        "This conversation could not be resumed. Please try again."
    ),
    "session_resolution_error": (
        "This conversation could not be resumed. Please try again."
    ),
}

# Terminal codes that mean "the stored provider_session_id is dead — mint a
# new one". Matches jvagent embed's ``interaction_not_created`` (with
# bootstrap ``session_resolution_error``) and any direct code of that name.
_SESSION_REBIND_CODES = frozenset(
    {"interaction_not_created", "session_resolution_error"}
)


def sanitize_error_envelope(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Return an ``error`` envelope safe to forward to the browser.

    Logs the raw message and replaces it with the user-facing text for
    codes known to carry ``f"{type(exc).__name__}: {exc}"``.
    """
    code = str(envelope.get("code") or "walker_failed")
    raw_message = envelope.get("message")
    replacement = USER_FACING_ERROR_MESSAGES.get(code)
    if replacement is None:
        return envelope
    logger.error(
        "embed.interact_stream terminal error code=%s raw=%s", code, raw_message
    )
    return {**envelope, "code": code, "message": replacement}


def _is_session_rebind_error(envelope: Dict[str, Any]) -> bool:
    if envelope.get("type") != "error":
        return False
    code = str(envelope.get("code") or "")
    if code in _SESSION_REBIND_CODES:
        return True
    details = envelope.get("details") or {}
    if (
        isinstance(details, dict)
        and details.get("bootstrap_error") in _SESSION_REBIND_CODES
    ):
        return True
    return False


async def _stream_one_embed_pass(
    *,
    resolved_agent_id: str,
    user_id: str,
    text: str,
    session_id: Optional[str],
    channel: str,
    filtered_data: Dict[str, Any],
    started: float,
    is_disconnected: Optional[Callable[[], Awaitable[bool]]],
    embed: Any,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield normalized events for a single ``interact_stream`` pass."""
    state = fresh_translator_state(started=started, run_id=filtered_data.get("run_id"))
    envelope_iter = embed.interact_stream(
        agent_id=resolved_agent_id,
        utterance=text,
        user_id=user_id,
        session_id=session_id,
        channel=channel,
        data=filtered_data,
        is_disconnected=is_disconnected,
    )
    async for envelope in envelope_iter:
        kind = envelope.get("type")

        if kind == "error":
            envelope = sanitize_error_envelope(envelope)
            yield envelope
            return

        if kind == "start":
            # Side-channel meta (consumed by ai_chat, not forwarded to
            # the browser) — captures jvagent's session id so the
            # thread row can persist it for resume on the next turn.
            yield {
                "type": "_meta",
                "provider_session_id": envelope.get("session_id"),
                "provider_interaction_id": envelope.get("interaction_id"),
                "provider_user_id": envelope.get("user_id"),
            }
            continue

        # All other envelopes flow through the same translator the HTTP
        # path uses — keeps the normalized event vocabulary in lockstep
        # across transports.
        async for ev in translate_envelope(envelope, state):
            yield ev

    # End-of-stream: flush any remaining tool_progress buffer the
    # translator accumulated. Without this the LAST tool's
    # progress event silently disappears (the per-call flush only
    # fires when a *next* envelope arrives). Mirrors the same
    # flush in the HTTP path's stream_jvagent_turn.
    if state.get("_progress_buffer"):
        for ev in _emit_tool_progress(state, state.get("_progress_segment_id")):
            yield ev


async def stream_jvagent_embed_turn(
    *,
    agent_id: str,
    user_id: str,
    text: str,
    session_id: Optional[str],
    channel: str,
    extra_data: Optional[Dict[str, Any]] = None,
    start_time: Optional[float] = None,
    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Run one streaming turn against the embedded jvagent runtime.

    Args:
        agent_id: jvspatial Agent node id (handed directly to
            ``embed.interact_stream``). Persisted on ``ChatThread.agent_id``
            at thread create; sourced from the catalog's ``AgentDescriptor.id``.
        user_id: Stable Integral user identifier.
        text: User utterance.
        session_id: Optional jvagent session id from the persisted thread.
        channel: Free-form channel hint forwarded to InteractActions.
        extra_data: Arbitrary dictionary merged into the walker's ``data``
            field (filtered of None values to match the HTTP path).
        start_time: ``time.monotonic()`` snapshot for end-to-end timing.
        is_disconnected: Async predicate polled by ``embed.interact_stream``;
            when it returns True the walker task is cancelled and the
            stream ends. Without it a client that navigated away leaves an
            orphan walker calling the model and dispatching tools.

    Yields:
        Normalized event dicts: ``_meta`` (session_id capture), then the
        usual ai_chat envelope (``text-delta`` / ``reasoning-delta`` /
        ``tool-call`` / ``source`` / ``status`` / ``step`` /
        ``message-finish``), or terminal ``error``.
    """
    started = start_time if start_time is not None else time.monotonic()

    try:
        from jvagent import embed
    except ImportError as exc:
        logger.error("jvagent not installed; embed provider cannot run: %s", exc)
        yield {
            "type": "error",
            "code": "embed_not_installed",
            "message": "jvagent package is not importable in this process",
        }
        return

    resolved_agent_id = (agent_id or "").strip()
    if not resolved_agent_id:
        yield {
            "type": "error",
            "code": "agent_id_required",
            "message": "stream_jvagent_embed_turn requires a non-empty agent_id",
        }
        return

    filtered_data: Dict[str, Any] = {
        k: v for k, v in (extra_data or {}).items() if v is not None
    }

    try:
        rebind_attempted = False
        active_session_id = session_id
        while True:
            orphaned = False
            async for ev in _stream_one_embed_pass(
                resolved_agent_id=resolved_agent_id,
                user_id=user_id,
                text=text,
                session_id=active_session_id,
                channel=channel,
                filtered_data=filtered_data,
                started=started,
                is_disconnected=is_disconnected,
                embed=embed,
            ):
                if (
                    not rebind_attempted
                    and active_session_id
                    and _is_session_rebind_error(ev)
                ):
                    orphaned = True
                    break
                yield ev

            if not orphaned:
                return

            rebind_attempted = True
            logger.warning(
                "embed session orphaned (agent_id=%s session_id=%s); "
                "clearing and retrying once with a fresh session",
                resolved_agent_id,
                active_session_id,
            )
            yield {"type": "_meta", "clear_provider_session": True}
            active_session_id = None
    except Exception as exc:
        logger.exception(
            "embed.interact_stream() failed for agent_id=%s",
            resolved_agent_id,
        )
        logger.error("embed_interact_failed raw=%s: %s", type(exc).__name__, exc)
        yield {
            "type": "error",
            "code": "embed_interact_failed",
            "message": USER_FACING_ERROR_MESSAGES["embed_interact_failed"],
        }


def cancel_interact(
    *,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> bool:
    """Cancel an in-flight embedded jvagent interact stream."""
    try:
        from jvagent import embed
    except ImportError:
        return False
    return embed.cancel_interact(session_id=session_id, thread_id=thread_id)
