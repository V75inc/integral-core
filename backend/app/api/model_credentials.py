"""Per-user model API credential (BYOK) endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional, cast

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.api.utils import resolve_principal_id
from app.schemas.model_credentials import (
    ModelCredentialResponse,
    ModelCredentialUpsertRequest,
    ModelCredentialValidateRequest,
    ModelCredentialValidateResponse,
    ModelProvider,
)
from app.services.change_event import emit_change_event
from app.services.model_credentials import (
    audit_snapshot_credential,
    get_active_credential_for_user,
    revoke_user_credential,
    upsert_user_credential,
    validate_provider_api_key,
)


def _to_response(record) -> ModelCredentialResponse:
    def _prov(value: str) -> Optional[ModelProvider]:
        v = (value or "").strip()
        # Stored slot providers are validated at upsert; safe to narrow.
        return cast(Optional[ModelProvider], v or None)

    return ModelCredentialResponse(
        provider=cast(ModelProvider, record.provider),
        model=record.model,
        light_provider=_prov(record.light_provider),
        light_model=record.light_model or None,
        heavy_provider=_prov(record.heavy_provider),
        heavy_model=record.heavy_model or None,
        vision_provider=_prov(record.vision_provider),
        vision_model=record.vision_model or None,
        speech_provider=_prov(record.speech_provider),
        speech_model=record.speech_model or None,
        key_fingerprint=record.key_fingerprint,
        light_key_fingerprint=record.light_key_fingerprint or None,
        heavy_key_fingerprint=record.heavy_key_fingerprint or None,
        vision_key_fingerprint=record.vision_key_fingerprint or None,
        speech_key_fingerprint=record.speech_key_fingerprint or None,
        is_active=bool(record.is_active),
        validated_at=record.validated_at,
        last_used_at=record.last_used_at,
        updated_at=record.updated_at,
    )


@endpoint(
    "/users/me/model-credentials",
    methods=["GET"],
    auth=True,
    tags=["Users"],
)
async def get_my_model_credential(request: Request) -> Dict[str, Any]:
    """Return metadata for the caller's active model credential (never the key)."""
    user_id = resolve_principal_id(request)
    record = await get_active_credential_for_user(user_id)
    if not record:
        return {"credential": None}
    return {"credential": _to_response(record).model_dump()}


@endpoint(
    "/users/me/model-credentials",
    methods=["POST"],
    auth=True,
    tags=["Users"],
)
async def upsert_my_model_credential(request: Request) -> Dict[str, Any]:
    """Validate and store the caller's model API key (write-only)."""
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError("Invalid JSON body") from exc
    try:
        body = ModelCredentialUpsertRequest.model_validate(raw or {})
    except Exception as exc:
        raise BadRequestError(str(exc)) from exc

    user_id = resolve_principal_id(request)
    before = await get_active_credential_for_user(user_id)
    try:
        record = await upsert_user_credential(
            user_id=user_id,
            provider=body.provider,
            model=body.model,
            api_key=body.api_key,
            light_model=body.light_model,
            light_provider=body.light_provider,
            light_api_key=body.light_api_key,
            heavy_model=body.heavy_model,
            heavy_provider=body.heavy_provider,
            heavy_api_key=body.heavy_api_key,
            vision_model=body.vision_model,
            vision_provider=body.vision_provider,
            vision_api_key=body.vision_api_key,
            speech_model=body.speech_model,
            speech_provider=body.speech_provider,
            speech_api_key=body.speech_api_key,
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except RuntimeError as exc:
        raise BadRequestError(str(exc)) from exc

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="model_credential.upsert",
        resource_type="UserModelCredential",
        resource_id=record.id,
        scope=user_id,
        before=audit_snapshot_credential(before) if before else None,
        after=audit_snapshot_credential(record),
    )
    return {"credential": _to_response(record).model_dump()}


@endpoint(
    "/users/me/model-credentials",
    methods=["DELETE"],
    auth=True,
    tags=["Users"],
)
async def delete_my_model_credential(request: Request) -> Dict[str, Any]:
    """Revoke the caller's active model credential."""
    user_id = resolve_principal_id(request)
    before = await get_active_credential_for_user(user_id)
    if not before:
        raise ResourceNotFoundError("No active model credential")
    revoked = await revoke_user_credential(user_id)
    if not revoked:
        raise ResourceNotFoundError("No active model credential")
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="model_credential.revoke",
        resource_type="UserModelCredential",
        resource_id=before.id,
        scope=user_id,
        before=audit_snapshot_credential(before),
        after={"is_active": False},
    )
    return {"revoked": True}


@endpoint(
    "/users/me/model-credentials/validate",
    methods=["POST"],
    auth=True,
    tags=["Users"],
)
async def validate_my_model_credential(request: Request) -> Dict[str, Any]:
    """Validate a provider API key without persisting it."""
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError("Invalid JSON body") from exc
    try:
        body = ModelCredentialValidateRequest.model_validate(raw or {})
    except Exception as exc:
        raise BadRequestError(str(exc)) from exc

    _ = resolve_principal_id(request)
    valid, message = await validate_provider_api_key(body.provider, body.api_key)
    return ModelCredentialValidateResponse(valid=valid, message=message).model_dump()
