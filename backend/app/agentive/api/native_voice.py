"""Native voice socket for authenticated Integral web chat."""

import asyncio
from contextlib import suppress
from hmac import compare_digest

from fastapi import (  # deviation: jvspatial @endpoint has no WebSocket primitive; only /ws/native-voice is registered here.
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
from app.schemas.voice_configuration import VoiceStart
from app.services.native_voice import (
    VoiceRelay,
    VoiceScope,
    authorize,
    open_upstream,
)
from app.services.permissions import get_user_node
from app.services.voice_configuration import resolve_realtime_credential
from app.services.ws_ticket import redeem_ws_ticket_shared

router = APIRouter()
_active_calls: set[str] = set()


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
    try:
        async with asyncio.timeout(8):
            payload = await browser.receive_text()
            if len(payload) > 2048:
                raise ValueError("Invalid request")
            opening = VoiceStart.model_validate_json(payload)
            principal = await redeem_ws_ticket_shared(opening.ticket)
            if (
                not principal
                or await get_user_node(principal) is None
                or principal in _active_calls
            ):
                await browser.close(code=4403)
                return
            _active_calls.add(principal)
            claimed = True
            scope = VoiceScope(
                principal, opening.workspace_id, opening.profile_key, opening.thread_id
            )
            await authorize(scope)
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
            await authorize(current)
            if binding:
                await binding.current()
            fresh = await resolve_realtime_credential(scope.workspace)
            if (
                fresh is None
                or fresh.model != credential.model
                or not compare_digest(fresh.api_key, credential.api_key)
            ):
                raise InsufficientPermissionsError(message="Voice settings changed")

        upstream = await open_upstream(credential)
        await VoiceRelay(browser, upstream, scope, check=still_authorized).run(
            identity.persona, name=identity.name
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
        if (
            browser.client_state
            == browser.application_state
            == WebSocketState.CONNECTED
        ):
            with suppress(RuntimeError, WebSocketDisconnect):
                await browser.close()
        if upstream is not None:
            with suppress(Exception):
                await upstream.close()
        if claimed:
            _active_calls.discard(principal)
