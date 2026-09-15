"""Voice input for the agent chat — effective config and session minting.

``GET /agentive/speech/config`` tells the composer which recognizers the
caller may use in the current workspace. ``POST /agentive/speech/session``
mints a short-lived browser credential for the workspace's provider; the
browser then talks to the vendor directly, so no audio passes through
Integral and the provider key never leaves the server.
"""

from typing import Any, Dict, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.agentive.services.speech import service as speech_service
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    JVSpatialAPIException,
    MissingAuthenticationError,
    RateLimitedError,
    ResourceConflictError,
    ServiceUnavailableError,
)
from app.api.utils import resolve_principal_id
from app.schemas.agentive.speech import SpeechSessionRequest
from app.services.request_scope import resolve_workspace_id_from_request

#: ``details.reason`` on the 409 so the composer can tell "no provider"
#: apart from other conflicts and fall back to the browser recognizer.
NOT_CONFIGURED_REASON = "speech_provider_not_configured"


async def _caller_and_workspace(request: Request) -> Tuple[str, str]:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if not workspace_id:
        raise BadRequestError(message="No workspace in scope")
    return user_id, workspace_id


def _http_error(exc: speech_service.SpeechError) -> JVSpatialAPIException:
    if isinstance(exc, speech_service.SpeechAccessDenied):
        return InsufficientPermissionsError(message=str(exc))
    if isinstance(exc, speech_service.SpeechNotConfigured):
        return ResourceConflictError(
            message=str(exc), details={"reason": NOT_CONFIGURED_REASON}
        )
    if isinstance(exc, speech_service.SpeechRateLimited):
        return RateLimitedError(message=str(exc))
    if isinstance(exc, speech_service.SpeechProviderFailure):
        if exc.code == "bad_request":
            return BadRequestError(message=exc.message)
        return ServiceUnavailableError(
            message=exc.message, details={"provider_error": exc.code}
        )
    return ServiceUnavailableError(message=str(exc))


@endpoint("/agentive/speech/config", methods=["GET"], auth=True, tags=["Agentive"])
async def get_speech_config(request: Request) -> Dict[str, Any]:
    """Recognizers this caller may use in the current workspace, in order."""
    user_id, workspace_id = await _caller_and_workspace(request)
    config = await speech_service.resolve_effective_config(user_id, workspace_id)
    return config.model_dump(mode="json")


@endpoint("/agentive/speech/session", methods=["POST"], auth=True, tags=["Agentive"])
async def create_speech_session(request: Request) -> Any:
    """Mint a short-lived browser credential for a live dictation session."""
    user_id, workspace_id = await _caller_and_workspace(request)

    raw: Any = {}
    if (await request.body()).strip():
        try:
            raw = await request.json()
        except ValueError as exc:
            raise BadRequestError(message="Invalid JSON body") from exc
    if not isinstance(raw, dict):
        raise BadRequestError(message="Body must be a JSON object")
    try:
        req = SpeechSessionRequest.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        raise BadRequestError(
            message=f"Invalid session request: {first.get('msg', 'invalid body')}"
        ) from exc

    try:
        session = await speech_service.mint_session(user_id, workspace_id, req)
    except speech_service.SpeechError as exc:
        raise _http_error(exc) from exc

    # The body is a live credential: keep it out of every cache.
    return JSONResponse(
        content=session.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )
