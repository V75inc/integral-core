"""Native voice socket for authenticated Integral web chat."""

import asyncio
import logging
from contextlib import suppress
from hmac import compare_digest

from fastapi import (  # deviation: jvspatial @endpoint has no WebSocket primitive; only /ws/native-voice and /ws/phone-voice are registered here.
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)
from starlette.websockets import WebSocketState

from app.agentive.services.profile_management import (
    open_published_profile_with_identity,
    workspace_voice_profile_identity,
)
from app.api.errors import InsufficientPermissionsError
from app.config import settings
from app.models.nodes import Workspace
from app.schemas.voice_configuration import VoiceStart
from app.services.native_voice import (
    BROWSER_CALL_ACTIONS,
    VoiceRelay,
    VoiceScope,
    authorize,
    open_phone_relay,
    open_upstream,
    voice_thread_continuity,
)
from app.services.native_voice_review_audio import ReviewVoice
from app.services.permissions import get_user_node
from app.services.user_timezone import greeting_period
from app.services.voice_configuration import resolve_realtime_credential
from app.services.ws_ticket import redeem_ws_ticket_shared

logger = logging.getLogger(__name__)
router = APIRouter()
# Per process. Another API worker does not see this set.
_active_calls: set[str] = set()


def principal_has_live_call(principal: str | None) -> bool:
    """True when this process already holds a relay for the principal."""
    return bool(principal) and principal in _active_calls


# deviation: jvspatial @endpoint does not support WebSocket route registration.
@router.websocket("/ws/native-voice")
async def native_voice(browser: WebSocket):
    """Authorize and relay one authenticated live voice call."""
    if not settings.INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED or browser.headers.get(
        "origin"
    ) != settings.FRONTEND_ORIGIN.rstrip("/"):
        await browser.close(code=4403)
        return
    await browser.accept()
    principal = None
    claimed = False
    upstream = None
    provider_closed = False
    try:
        async with asyncio.timeout(8):
            payload = await browser.receive_text()
            if len(payload) > 2048:
                raise ValueError("Invalid request")
            opening = VoiceStart.model_validate_json(payload)
            principal = await redeem_ws_ticket_shared(opening.ticket)
            caller = await get_user_node(principal) if principal else None
            if caller is None:
                await browser.close(code=4403)
                return
            if principal_has_live_call(principal):
                await browser.send_json(
                    {
                        "type": "error",
                        "message": "You already have a live voice call. End that call before starting another.",
                    }
                )
                await browser.close(code=4409)
                return
            _active_calls.add(principal)
            claimed = True
            scope = VoiceScope(
                principal,
                opening.workspace_id,
                opening.profile_key,
                opening.thread_id,
                actions=BROWSER_CALL_ACTIONS,
            )
            thread = await authorize(scope)
            continuity = voice_thread_continuity(thread)
            credential = await resolve_realtime_credential(scope.workspace)
            if credential is None:
                await browser.send_json(
                    {
                        "type": "error",
                        "message": "Configure a live voice model in AI Models.",
                    }
                )
                await browser.close(code=4404)
                return
            binding = None
            if scope.profile:
                binding, identity = await open_published_profile_with_identity(
                    scope.workspace,
                    "native-chat",
                    principal,
                    native_profile_key=scope.profile,
                )
            else:
                identity = await workspace_voice_profile_identity(scope.workspace)

        async def still_authorized(current):
            current_thread = await authorize(current)
            if binding:
                await binding.current()
            fresh = await resolve_realtime_credential(scope.workspace)
            if (
                fresh is None
                or fresh.model != credential.model
                or not compare_digest(fresh.api_key, credential.api_key)
            ):
                raise InsufficientPermissionsError(message="Voice settings changed")
            return current_thread

        # Until Ready the browser sends no more frames. Race provider dialing
        # against a refresh/disconnect so an abandoned call cannot open upstream.
        async def connect_provider():
            nonlocal upstream
            upstream = await open_upstream(credential)
            if upstream is not None:
                logger.info("Native voice transport opened")

        dial = asyncio.create_task(connect_provider())
        browser_handshake = asyncio.create_task(browser.receive())
        try:
            done, _ = await asyncio.wait(
                (dial, browser_handshake), return_when=asyncio.FIRST_COMPLETED
            )
            if dial in done:
                dial.result()
        finally:
            for task in (dial, browser_handshake):
                if not task.done():
                    task.cancel()
            first_frame = (
                await asyncio.gather(dial, browser_handshake, return_exceptions=True)
            )[1]
        if isinstance(first_frame, dict):
            if first_frame.get("type") == "websocket.disconnect":
                return
            raise ValueError("Voice frames are not accepted before readiness")
        if upstream is None:
            raise WebSocketDisconnect()
        # The display name is the only workspace fact needed to orient a new call.
        # Recheck the bound identity and membership after the provider handshake.
        await still_authorized(scope)
        workspace = await Workspace.get(scope.workspace)
        workspace_label = " ".join(workspace.name.split())[:100] if workspace else ""
        await VoiceRelay(
            browser,
            upstream,
            scope,
            check=still_authorized,
            review_voice=ReviewVoice(lambda: open_upstream(credential)),
            review_confirmation_supported=opening.review_confirmation,
            conversational_review=opening.conversational_review,
        ).run(
            identity.persona,
            name=identity.name,
            continuity=continuity,
            workspace_label=workspace_label,
            greeting_period=greeting_period(getattr(caller, "timezone", None)),
        )
    except WebSocketDisconnect:
        pass
    except Exception:
        with suppress(RuntimeError, WebSocketDisconnect):
            await browser.send_json(
                {"type": "error", "message": "The live voice call could not continue."}
            )
            await browser.close(code=4400)
    finally:
        try:
            if (
                browser.client_state
                == browser.application_state
                == WebSocketState.CONNECTED
            ):
                with suppress(RuntimeError, WebSocketDisconnect):
                    await browser.close()
        finally:
            try:
                if upstream is not None:
                    with suppress(Exception):
                        await upstream.close()
                        provider_closed = True
            finally:
                if claimed:
                    _active_calls.discard(principal)
                if upstream is not None:
                    logger.info(
                        "Native voice transport released; provider_closed=%s",
                        provider_closed,
                    )


class _PhoneSocket:
    """Browser-shaped peer for one handset. Playback bytes return on the socket.

    Navigation is a page move. The handset drops it. This is telephone
    admission beyond ADR 019's current release.
    """

    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket

    async def receive(self):
        return await self._socket.receive()

    async def send_bytes(self, data: bytes) -> None:
        await self._socket.send_bytes(data)

    async def send_json(self, message: dict) -> None:
        if message.get("type") == "navigation":
            return
        await self._socket.send_json(message)


async def _hang_up_phone_quietly(call_id: str, upstream=None) -> None:
    """Disconnect one phone call without escaping the socket finally.

    ``hang_up_phone_call`` already closes its stored upstream once. A later
    close here runs only when that helper raises before the close.
    """
    from app.agentive.services.whatsapp_voice_bridge import hang_up_phone_call

    try:
        await hang_up_phone_call(call_id)
    except Exception:
        logger.warning("phone voice hangup failed")
        close = getattr(upstream, "close", None)
        if close is not None:
            with suppress(Exception):
                await close()


# deviation: jvspatial @endpoint has no WebSocket primitive.
@router.websocket("/ws/phone-voice")
async def phone_voice(socket: WebSocket):
    """Relay one phone peer. This is telephone admission beyond ADR 019's current release.

    The phone socket does not check a browser origin and does not redeem a
    VoiceStart ticket. It admits only the one-call bearer.
    """
    from app.agentive.services.whatsapp_voice_bridge import (
        attach_phone_relay,
        discard_relay_bearer,
        relay_bearer_record,
    )

    presented = socket.headers.get("x-integral-phone-bearer") or ""
    record = relay_bearer_record(presented)
    if record is None:
        await socket.close(code=4403)
        return
    await socket.accept()
    principal = record["principal_id"]
    claimed = False
    upstream = None
    try:
        if principal_has_live_call(principal):
            await socket.close(code=4403)
            return
        _active_calls.add(principal)
        claimed = True
        if record.get("binding_id"):
            from app.agentive.services.whatsapp_voice_bridge import (
                confirm_phone_line,
            )

            if not await confirm_phone_line(record):
                await socket.close(code=4403)
                return
        credential = await resolve_realtime_credential(record["workspace_id"])
        if credential is None:
            await socket.close(code=4404)
            return
        upstream = await open_upstream(credential)
        if upstream is None:
            await socket.close(code=4404)
            return
        # Upstream is open and this principal is claimed. Hangup from either
        # side closes this relay. A missing bearer never reaches here.
        attach_phone_relay(
            call_id=record["call_id"],
            upstream=upstream,
            bearer=presented,
        )
        # An older row without the key is empty, so ``authorize`` refuses the
        # ``wa:`` thread rather than guessing a channel session.
        scope = VoiceScope(
            principal,
            record["workspace_id"],
            "",
            record["thread_id"],
            channel_session=record.get("channel_session", ""),
        )
        relay = open_phone_relay(_PhoneSocket(socket), upstream, scope, check=authorize)
        try:
            await relay.run()
        except WebSocketDisconnect:
            pass
    except WebSocketDisconnect:
        pass
    finally:
        try:
            # The bearer was valid, so this WhatsApp leg was accepted. Close
            # it on every exit: a live principal, a missing credential, an
            # upstream failure, or the relay ending. One call id disconnects
            # once. Discard still runs when hangup is stubbed.
            await _hang_up_phone_quietly(record["call_id"], upstream)
            discard_relay_bearer(record["call_id"])
        finally:
            if claimed:
                _active_calls.discard(principal)
            if (
                socket.client_state
                == socket.application_state
                == WebSocketState.CONNECTED
            ):
                with suppress(RuntimeError, WebSocketDisconnect):
                    await socket.close()
