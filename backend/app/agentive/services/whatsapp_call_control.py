"""WhatsApp call control: calls parsing and jvvoice accept or disconnect."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.agentive.services.whatsapp_call_admission import (
    admit_whatsapp_caller,
    admit_whatsapp_visitor_caller,
)
from app.config import settings
from app.exceptions import BadRequestError
from app.schemas.agentive.whatsapp_call import (
    JvvoiceAcceptCallRequest,
    JvvoiceDisconnectCallRequest,
)
from app.services.url_safety import pin_public_dns, validate_outbound_http_url

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0
# Process-local call ids this process is accepting, has accepted, or has
# refused. Another worker does not see this set. Same lifetime as the phone
# relay maps. Tests clear it; a new process starts empty.
_process_call_claims: set[str] = set()


def clear_process_call_claims() -> None:
    """Drop in-process call claims. Tests use this between cases."""
    _process_call_claims.clear()


def _claim_process_call(call_id: str) -> bool:
    """Claim one call id. False when this process already holds it."""
    if not call_id or call_id in _process_call_claims:
        return False
    _process_call_claims.add(call_id)
    return True


@dataclass(frozen=True)
class ParsedWhatsAppCall:
    """First ``calls`` change. Actionable calls carry SDP. Not a text class."""

    actionable: bool
    call_id: str = ""
    caller: str = ""
    sdp: str = ""
    sdp_type: str = ""
    phone_number_id: str = ""
    event: str = ""
    is_calls: bool = False


def parse_whatsapp_call(payload: dict) -> ParsedWhatsAppCall:
    """Read the first ``field: calls`` change and its first call object.

    A call is actionable only when the id, caller, and session SDP are
    non-empty. ``sdp_type`` defaults to ``offer`` when the session omits it.
    Status-only payloads and events without SDP are not actionable. Event
    names are not filtered.
    """
    value = _first_calls_value(payload)
    if value is None:
        return ParsedWhatsAppCall(actionable=False, is_calls=False)
    phone_number_id = _metadata_phone_number_id(value)
    call = _first_dict(value.get("calls"))
    if call is None:
        return ParsedWhatsAppCall(
            actionable=False,
            phone_number_id=phone_number_id,
            is_calls=True,
        )
    call_id = _text(call.get("id"))
    caller = _text(call.get("from"))
    event = _text(call.get("event"))
    session = call.get("session")
    sdp = ""
    sdp_type = ""
    if isinstance(session, dict):
        raw_sdp = session.get("sdp")
        # Do not strip SDP. Trailing CRLF is part of the document; Pion
        # rejects a stripped offer with ``unmarshal SDP: EOF``.
        sdp = raw_sdp if isinstance(raw_sdp, str) else ""
        sdp_type = _text(session.get("sdp_type")) or "offer"
    if not call_id or not caller or not sdp.strip():
        return ParsedWhatsAppCall(
            actionable=False,
            call_id=call_id,
            caller=caller,
            phone_number_id=phone_number_id,
            event=event,
            is_calls=True,
        )
    return ParsedWhatsAppCall(
        actionable=True,
        call_id=call_id,
        caller=caller,
        sdp=sdp,
        sdp_type=sdp_type or "offer",
        phone_number_id=phone_number_id,
        event=event,
        is_calls=True,
    )


def _first_calls_value(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "calls":
                continue
            value = change.get("value")
            if isinstance(value, dict):
                return value
            return None
    return None


def _first_dict(value: object) -> dict | None:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if isinstance(first, dict):
        return first
    return None


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _metadata_phone_number_id(value: dict) -> str:
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return _text(metadata.get("phone_number_id"))


@dataclass(frozen=True)
class JvvoiceCallResult:
    """One jvvoice HTTP outcome. ``ok`` is true only for the expected status."""

    ok: bool
    status_code: int
    status: str = ""
    error: str = ""
    detail: str = ""


_SECRET_MARK = re.compile(
    r"(EAAG|jvv_|jvk_|whsec_)[A-Za-z0-9_\-]{4,}",
    re.IGNORECASE,
)


def _safe_error_text(value: object) -> str:
    """Keep a short provider message. Never log tokens, SDP, or bearers."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 200:
        return ""
    lowered = text.lower()
    if "bearer " in lowered or "v=0" in lowered or _SECRET_MARK.search(text):
        return ""
    if text.count(".") == 2 and len(text) > 40:
        return ""
    compact = "".join(text.split())
    if len(compact) >= 20 and compact.isalnum():
        return ""
    return text


def _provider_detail(data: Optional[dict[str, Any]], *, status_code: int) -> str:
    """Prefer a safe error string. 422 still uses loc/msg only."""
    if status_code == 422:
        summary = _validation_summary(data)
        if summary:
            return summary
    if not isinstance(data, dict):
        return ""
    for key in ("error", "detail", "message"):
        text = _safe_error_text(data.get(key))
        if text:
            return text
    return ""


def _validation_summary(data: Optional[dict[str, Any]]) -> str:
    """Keep FastAPI loc/msg only. Never log ``input``, SDP, tokens, or keys."""
    if not isinstance(data, dict):
        return ""
    detail = data.get("detail")
    if not isinstance(detail, list):
        return ""
    parts: list[str] = []
    for item in detail:
        if not isinstance(item, dict):
            continue
        msg = item.get("msg")
        if not isinstance(msg, str):
            continue
        msg = msg.strip()
        if not msg or len(msg) > 200:
            continue
        loc = item.get("loc")
        loc_s = ""
        if isinstance(loc, list):
            loc_s = ".".join(str(p) for p in loc if isinstance(p, (str, int)))
        parts.append(f"{loc_s}:{msg}" if loc_s else msg)
        if len(parts) >= 4:
            break
    return "; ".join(parts)


def _log_jvvoice(operation: str, result: JvvoiceCallResult) -> None:
    """Log the operation and HTTP status. Never the bearer, token, or SDP."""
    if result.ok:
        logger.info("jvvoice %s succeeded", operation)
        return
    if result.detail:
        logger.warning(
            "jvvoice %s failed status=%s detail=%s",
            operation,
            result.status_code,
            result.detail,
        )
        return
    logger.warning("jvvoice %s failed status=%s", operation, result.status_code)


def _object_from_response(response: httpx.Response) -> Optional[dict[str, Any]]:
    raw = response.content
    if not raw or not raw.strip():
        return {}
    try:
        parsed = response.json()
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _result_from_response(
    response: httpx.Response,
    *,
    success_status: Optional[str],
) -> JvvoiceCallResult:
    status_code = response.status_code
    data = _object_from_response(response)
    body_status = ""
    if isinstance(data, dict):
        raw_status = data.get("status")
        if isinstance(raw_status, str):
            body_status = raw_status.strip()
    if data is None:
        return JvvoiceCallResult(
            ok=False,
            status_code=status_code,
            error="invalid JSON response",
        )
    if not 200 <= status_code < 300:
        return JvvoiceCallResult(
            ok=False,
            status_code=status_code,
            status=body_status,
            error=f"HTTP {status_code}",
            detail=_provider_detail(data, status_code=status_code),
        )
    if success_status is not None and body_status != success_status:
        return JvvoiceCallResult(
            ok=False,
            status_code=status_code,
            status=body_status,
            error="unexpected status",
            detail=_provider_detail(data, status_code=status_code),
        )
    return JvvoiceCallResult(
        ok=True,
        status_code=status_code,
        status=body_status,
    )


async def _perform_jvvoice(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    success_status: Optional[str],
) -> JvvoiceCallResult:
    try:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            json=payload,
        )
    except httpx.RequestError:
        return JvvoiceCallResult(ok=False, status_code=0, error="request failed")
    return _result_from_response(response, success_status=success_status)


def _voice_service(binding) -> tuple[str, str]:
    """Use the line's jvvoice URL and key.

    A saved URL or ciphertext that cannot both be used fails closed. It does
    not fall through to the deployment ``JVVOICE_*`` settings.
    """
    if binding is not None:
        url = (getattr(binding, "jvvoice_base_url", "") or "").strip().rstrip("/")
        ciphertext = getattr(binding, "jvvoice_api_key_ciphertext", "") or ""
        if url or ciphertext:
            key = ""
            if ciphertext:
                from app.services.credential_crypto import decrypt_secret_from_storage

                try:
                    key = decrypt_secret_from_storage(ciphertext, aad=binding.id)
                except Exception:
                    key = ""
            if url and key:
                return url, key
            return "", ""
    return (
        (settings.JVVOICE_BASE_URL or "").strip().rstrip("/"),
        settings.JVVOICE_API_KEY or "",
    )


async def _post_jvvoice(
    path: str,
    payload: dict[str, Any],
    *,
    client: Optional[httpx.AsyncClient],
    operation: str,
    success_status: Optional[str],
    binding=None,
) -> JvvoiceCallResult:
    base, api_key = _voice_service(binding)
    url = f"{base}{path}"
    if client is None:
        try:
            await validate_outbound_http_url(url)
        except BadRequestError:
            result = JvvoiceCallResult(
                ok=False,
                status_code=0,
                error="host not allowed",
            )
            _log_jvvoice(operation, result)
            return result
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as owned:
            with pin_public_dns(url):
                result = await _perform_jvvoice(
                    owned,
                    url,
                    api_key,
                    payload,
                    success_status=success_status,
                )
    else:
        result = await _perform_jvvoice(
            client,
            url,
            api_key,
            payload,
            success_status=success_status,
        )
    _log_jvvoice(operation, result)
    return result


async def accept_jvvoice_call(
    *,
    whatsapp_call_id: str,
    phone_number_id: str,
    whatsapp_api_key: str,
    sdp: str,
    sdp_type: str = "offer",
    caller_phone: str,
    client: Optional[httpx.AsyncClient] = None,
    bridge: Optional[str] = None,
    relay_url: Optional[str] = None,
    relay_bearer: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    binding=None,
) -> JvvoiceCallResult:
    """POST one accept. Success requires a body status of ``connected``."""
    body = JvvoiceAcceptCallRequest(
        whatsapp_call_id=whatsapp_call_id,
        phone_number_id=phone_number_id,
        whatsapp_api_key=whatsapp_api_key,
        sdp=sdp,
        sdp_type=sdp_type or "offer",
        caller_phone=caller_phone,
        bridge=bridge,
        relay_url=relay_url,
        relay_bearer=relay_bearer,
        session_id=session_id,
        user_id=user_id,
    )
    return await _post_jvvoice(
        "/api/calls/accept",
        body.model_dump(exclude_none=True),
        client=client,
        operation="accept",
        success_status="connected",
        binding=binding,
    )


async def disconnect_jvvoice_call(
    *,
    whatsapp_call_id: str,
    whatsapp_api_key: str = "",
    client: Optional[httpx.AsyncClient] = None,
    binding=None,
) -> JvvoiceCallResult:
    """POST one disconnect with ``user_initiated`` false. Does not accept."""
    body = JvvoiceDisconnectCallRequest(
        whatsapp_call_id=whatsapp_call_id,
        user_initiated=False,
        whatsapp_api_key=whatsapp_api_key,
    )
    return await _post_jvvoice(
        "/api/calls/disconnect",
        body.model_dump(),
        client=client,
        operation="disconnect",
        success_status=None,
        binding=binding,
    )


async def release_busy_phone_call(
    *,
    whatsapp_call_id: str,
    whatsapp_api_key: str = "",
    client: Optional[httpx.AsyncClient] = None,
    binding=None,
) -> JvvoiceCallResult:
    """Hang up a phone call that cannot join an existing live relay.

    Uses the disconnect helper. Does not post accept. ``binding`` selects the
    line's jvvoice host.
    """
    return await disconnect_jvvoice_call(
        whatsapp_call_id=whatsapp_call_id,
        whatsapp_api_key=whatsapp_api_key,
        client=client,
        binding=binding,
    )


class _GraphCallReceipts:
    """Production receipt store. ``WhatsAppInboundReceipt`` is an Object."""

    async def find(self, binding_id: str, meta_message_id: str):
        from app.models.whatsapp_inbound import WhatsAppInboundReceipt

        return await WhatsAppInboundReceipt.find(
            binding_id=binding_id,
            meta_message_id=meta_message_id,
        )

    async def create(self, **fields):
        from app.models.whatsapp_inbound import WhatsAppInboundReceipt
        from app.utils.time import utc_now_iso

        return await WhatsAppInboundReceipt.create(
            binding_id=fields.get("binding_id", ""),
            meta_message_id=fields.get("meta_message_id", ""),
            kind=fields.get("kind", "call"),
            status=fields.get("status", ""),
            last_error=fields.get("last_error", ""),
            created_at=utc_now_iso(),
        )


def _visitor_ready_workspaces(grants: list) -> tuple[str, ...]:
    """Workspaces whose public profile may answer a call. Names stay out."""
    ready = []
    for grant in grants:
        if not getattr(grant, "enabled", False):
            continue
        if not getattr(grant, "calling_enabled", False):
            continue
        if not getattr(grant, "public_profile_ready", False):
            continue
        workspace_id = str(getattr(grant, "workspace_id", "") or "").strip()
        if workspace_id:
            ready.append(workspace_id)
    return tuple(ready)


def _admission_inputs(
    binding, identities: list, grants: list, caller: str = ""
) -> dict:
    """Turn already-loaded identities and grants into pure-function inputs.

    Several verified numbers on one account are not ambiguous by themselves.
    The inbound caller selects the matching number. Ambiguous means that
    caller matches more than one verified identity.
    """
    from app.agentive.services.whatsapp_call_admission import _comparable_e164

    wanted = _comparable_e164(caller)
    matched = []
    for identity in identities:
        number = _comparable_e164(str(getattr(identity, "channel_user_id", "") or ""))
        if wanted and number == wanted:
            matched.append((identity, number))
    if len(matched) > 1:
        ambiguous_verified = True
        verified_e164 = ""
        principal_id = ""
    elif len(matched) == 1:
        identity, number = matched[0]
        ambiguous_verified = False
        verified_e164 = number
        principal_id = str(getattr(identity, "user_id", "") or "")
    elif len(identities) == 1 and not wanted:
        identity = identities[0]
        ambiguous_verified = False
        verified_e164 = str(getattr(identity, "channel_user_id", "") or "")
        principal_id = str(getattr(identity, "user_id", "") or "")
    elif len(identities) > 1 and not wanted:
        ambiguous_verified = True
        verified_e164 = ""
        principal_id = ""
    else:
        ambiguous_verified = False
        verified_e164 = ""
        principal_id = ""
    enabled = [grant for grant in grants if getattr(grant, "enabled", False)]
    if len(enabled) > 1:
        ambiguous_workspace = True
        workspace_id = ""
        calling_enabled = False
    elif len(enabled) == 1:
        grant = enabled[0]
        ambiguous_workspace = False
        workspace_id = str(getattr(grant, "workspace_id", "") or "")
        calling_enabled = bool(getattr(grant, "calling_enabled", False))
    else:
        ambiguous_workspace = False
        workspace_id = ""
        calling_enabled = False
    return {
        "verified_e164": verified_e164,
        "line_enabled": bool(getattr(binding, "enabled", False)),
        "calling_enabled": calling_enabled,
        "ambiguous_verified_number": ambiguous_verified,
        "workspace_id": workspace_id,
        "ambiguous_workspace": ambiguous_workspace,
        "principal_id": principal_id,
    }


def _line_answer_still_holds(binding, parsed, identities, grants, admission) -> bool:
    """True when a fresh read still answers this caller on this line."""
    if (parsed.phone_number_id or "") != (
        getattr(binding, "phone_number_id", "") or ""
    ):
        return False
    if not (admission.principal_id or "").strip():
        return False
    fresh = admit_whatsapp_caller(
        parsed.caller,
        **_admission_inputs(binding, identities, grants, caller=parsed.caller),
    )
    return (
        fresh.decision == "answer"
        and fresh.principal_id == admission.principal_id
        and fresh.workspace_id == admission.workspace_id
        and fresh.verified_e164 == admission.verified_e164
    )


async def _record_call(
    store, binding, parsed: ParsedWhatsAppCall, status: str, last_error: str
):
    if store is None or not parsed.call_id:
        return
    await store.create(
        binding_id=getattr(binding, "id", ""),
        meta_message_id=parsed.call_id,
        kind="call",
        status=status,
        last_error=last_error,
    )


async def _calling_token(
    binding, api_key: str, client: Optional[httpx.AsyncClient]
) -> str:
    from app.agentive.clients.jvconnect_whatsapp import fetch_calling_credentials

    if not api_key or not getattr(binding, "jvconnect_base_url", ""):
        return ""
    result = await fetch_calling_credentials(
        binding.jvconnect_base_url,
        api_key,
        client=client,
    )
    if not result.ok:
        return ""
    token = result.data.get("access_token")
    if not isinstance(token, str):
        return ""
    return token.strip()


async def _refuse_public_call(
    store,
    binding,
    parsed: ParsedWhatsAppCall,
    reason: str,
    *,
    http_client: Optional[httpx.AsyncClient],
    whatsapp_api_key: str,
) -> None:
    """Record a public-call refusal and disconnect. Does not open member voice."""
    from app.agentive.services.whatsapp_voice_bridge import discard_relay_bearer

    discard_relay_bearer(parsed.call_id)
    await _record_call(store, binding, parsed, "refused", reason)
    if whatsapp_api_key:
        await disconnect_jvvoice_call(
            whatsapp_call_id=parsed.call_id,
            whatsapp_api_key=whatsapp_api_key,
            client=http_client,
            binding=binding,
        )
    logger.info("whatsapp call refused reason=%s", reason)


async def _accept_public_profile_call(
    store,
    binding,
    parsed: ParsedWhatsAppCall,
    workspace_id: str,
    *,
    jvconnect_api_key: str,
    http_client: Optional[httpx.AsyncClient],
    access_token_in_hand: str,
) -> None:
    """Accept one unknown caller on the public speech socket.

    This does not call the member relay, a chat thread, or a signed-in principal.
    """
    from app.agentive.services.whatsapp_public_profile import public_call_grant
    from app.agentive.services.whatsapp_voice_bridge import (
        discard_relay_bearer,
        mint_public_relay_bearer,
        public_phone_relay_url,
        remember_call_token,
    )

    grant = await public_call_grant(binding, workspace_id)
    if grant is None:
        await _refuse_public_call(
            store,
            binding,
            parsed,
            "admission_changed",
            http_client=http_client,
            whatsapp_api_key=access_token_in_hand,
        )
        return
    token = await _calling_token(binding, jvconnect_api_key, http_client)
    if not token:
        await _record_call(store, binding, parsed, "failed", "credentials_failed")
        logger.warning("whatsapp call credentials failed")
        return
    fresh = await public_call_grant(binding, workspace_id)
    if (
        fresh is None
        or fresh.grant_id != grant.grant_id
        or fresh.revision != grant.revision
        or fresh.profile_key != grant.profile_key
        or fresh.published_version != grant.published_version
    ):
        await _refuse_public_call(
            store,
            binding,
            parsed,
            "admission_changed",
            http_client=http_client,
            whatsapp_api_key=token,
        )
        return
    bearer = mint_public_relay_bearer(
        call_id=parsed.call_id,
        workspace_id=fresh.workspace_id,
        binding_id=getattr(binding, "id", "") or "",
        grant_id=fresh.grant_id,
        revision=fresh.revision,
        profile_key=fresh.profile_key,
        published_version=fresh.published_version,
    )
    relay_url = public_phone_relay_url()
    if not (relay_url.startswith("ws://") or relay_url.startswith("wss://")):
        discard_relay_bearer(parsed.call_id)
        await _record_call(store, binding, parsed, "failed", "relay_url_unavailable")
        logger.warning("whatsapp call relay url unavailable")
        return
    logger.info("whatsapp public call accept sdp_len=%s", len(parsed.sdp))
    accepted = await accept_jvvoice_call(
        whatsapp_call_id=parsed.call_id,
        phone_number_id=parsed.phone_number_id,
        whatsapp_api_key=token,
        sdp=parsed.sdp,
        sdp_type=parsed.sdp_type or "offer",
        caller_phone=parsed.caller,
        client=http_client,
        binding=binding,
        bridge="integral_native",
        relay_url=relay_url,
        relay_bearer=bearer,
    )
    if not accepted.ok:
        discard_relay_bearer(parsed.call_id)
        await _record_call(store, binding, parsed, "failed", "not_connected")
        logger.warning("whatsapp call accept failed status=%s", accepted.status_code)
        return
    remember_call_token(
        parsed.call_id,
        token,
        binding_id=getattr(binding, "id", "") or "",
    )
    await _record_call(store, binding, parsed, "accepted", "")
    logger.info("whatsapp public call accepted")


async def handle_whatsapp_call(
    binding,
    parsed: ParsedWhatsAppCall,
    identities: list,
    grants: list,
    *,
    jvconnect_api_key: str,
    http_client: Optional[httpx.AsyncClient] = None,
    receipt_store=None,
    access_token_in_hand: str = "",
    reload_call_inputs=None,
) -> dict:
    """Admit one calls payload, then accept, refuse, or hang up an accepted id."""
    if not parsed.is_calls:
        return {"ok": True}
    store = receipt_store if receipt_store is not None else _GraphCallReceipts()
    existing: list = []
    if parsed.call_id:
        found = await store.find(getattr(binding, "id", ""), parsed.call_id)
        if isinstance(found, list):
            existing = found
        elif found:
            existing = [found]
    if not parsed.actionable:
        # A later calls event with no SDP is hangup for an accepted id, not
        # a second accept. Disconnect failures stay a logged status.
        if any(getattr(row, "status", "") == "accepted" for row in existing):
            from app.agentive.services.whatsapp_voice_bridge import (
                hang_up_phone_call,
            )

            await hang_up_phone_call(
                parsed.call_id,
                whatsapp_api_key=access_token_in_hand,
                client=http_client,
                binding=binding,
            )
        return {"ok": True}
    # A stored receipt or an in-process claim means this id is already
    # decided. Claim before any further await so two deliveries cannot both
    # pass the empty find and both accept.
    if existing or not _claim_process_call(parsed.call_id):
        return {"ok": True}
    if (parsed.phone_number_id or "") != (
        getattr(binding, "phone_number_id", "") or ""
    ):
        await _record_call(store, binding, parsed, "refused", "line_mismatch")
        if access_token_in_hand:
            await disconnect_jvvoice_call(
                whatsapp_call_id=parsed.call_id,
                whatsapp_api_key=access_token_in_hand,
                client=http_client,
                binding=binding,
            )
        logger.info("whatsapp call refused reason=line_mismatch")
        return {"ok": True}
    admission = admit_whatsapp_caller(
        parsed.caller,
        **_admission_inputs(binding, identities, grants, caller=parsed.caller),
    )
    if admission.decision != "answer" and admission.reason in {
        "caller_mismatch",
        "no_verified_number",
    }:
        visitor = admit_whatsapp_visitor_caller(
            parsed.caller,
            line_enabled=bool(getattr(binding, "enabled", False)),
            ready_workspace_ids=_visitor_ready_workspaces(grants),
        )
        if visitor.decision == "answer":
            await _accept_public_profile_call(
                store,
                binding,
                parsed,
                visitor.workspace_id,
                jvconnect_api_key=jvconnect_api_key,
                http_client=http_client,
                access_token_in_hand=access_token_in_hand,
            )
            return {"ok": True}
    if admission.decision != "answer":
        await _record_call(store, binding, parsed, "refused", admission.reason)
        if access_token_in_hand:
            await disconnect_jvvoice_call(
                whatsapp_call_id=parsed.call_id,
                whatsapp_api_key=access_token_in_hand,
                client=http_client,
                binding=binding,
            )
        logger.info("whatsapp call refused reason=%s", admission.reason)
        return {"ok": True}
    if not (admission.principal_id or "").strip():
        await _record_call(store, binding, parsed, "refused", "no_principal")
        if access_token_in_hand:
            await disconnect_jvvoice_call(
                whatsapp_call_id=parsed.call_id,
                whatsapp_api_key=access_token_in_hand,
                client=http_client,
                binding=binding,
            )
        logger.info("whatsapp call refused reason=no_principal")
        return {"ok": True}
    token = await _calling_token(binding, jvconnect_api_key, http_client)
    if not token:
        await _record_call(store, binding, parsed, "failed", "credentials_failed")
        logger.warning("whatsapp call credentials failed")
        return {"ok": True}
    from app.agentive.services.whatsapp_voice_bridge import (
        bind_phone_accept,
        discard_relay_bearer,
        remember_call_token,
        start_phone_relay,
    )

    # Busy check before a bearer is minted. No open_upstream: a live
    # principal is disconnected and refused, and this call is not accepted.
    if not await start_phone_relay(
        principal=admission.principal_id,
        whatsapp_call_id=parsed.call_id,
        whatsapp_api_key=token,
        client=http_client,
        binding=binding,
    ):
        await _record_call(store, binding, parsed, "refused", "principal_busy")
        logger.info("whatsapp call refused reason=principal_busy")
        return {"ok": True}
    # The bearer is bound before the post so jvvoice can present it on the
    # phone socket. A status other than connected discards it. This does not
    # open that socket or wait for a model.
    try:
        fields = await bind_phone_accept(
            admission,
            parsed.call_id,
            binding_id=getattr(binding, "id", "") or "",
        )
    except ValueError as exc:
        discard_relay_bearer(parsed.call_id)
        reason = (
            "line_required" if "line is required" in str(exc) else "thread_mismatch"
        )
        await _record_call(store, binding, parsed, "failed", reason)
        logger.warning("whatsapp call %s", reason)
        return {"ok": True}
    relay_url = fields["relay_url"]
    logger.info("whatsapp call accept sdp_len=%s", len(parsed.sdp))
    if not (relay_url.startswith("ws://") or relay_url.startswith("wss://")):
        discard_relay_bearer(parsed.call_id)
        await _record_call(store, binding, parsed, "failed", "relay_url_unavailable")
        logger.warning("whatsapp call relay url unavailable")
        return {"ok": True}
    if reload_call_inputs is not None:
        identities, grants = await reload_call_inputs(binding)
        from app.agentive.nodes import WhatsAppChannelBinding

        try:
            fresh = await WhatsAppChannelBinding.get(getattr(binding, "id", "") or "")
        except RuntimeError:
            fresh = binding
        if fresh is None:
            discard_relay_bearer(parsed.call_id)
            await _record_call(store, binding, parsed, "refused", "admission_changed")
            if token:
                await disconnect_jvvoice_call(
                    whatsapp_call_id=parsed.call_id,
                    whatsapp_api_key=token,
                    client=http_client,
                    binding=binding,
                )
            logger.info("whatsapp call refused reason=admission_changed")
            return {"ok": True}
        binding = fresh
    if not _line_answer_still_holds(binding, parsed, identities, grants, admission):
        discard_relay_bearer(parsed.call_id)
        await _record_call(store, binding, parsed, "refused", "admission_changed")
        if token:
            await disconnect_jvvoice_call(
                whatsapp_call_id=parsed.call_id,
                whatsapp_api_key=token,
                client=http_client,
                binding=binding,
            )
        logger.info("whatsapp call refused reason=admission_changed")
        return {"ok": True}
    accepted = await accept_jvvoice_call(
        whatsapp_call_id=parsed.call_id,
        phone_number_id=parsed.phone_number_id,
        whatsapp_api_key=token,
        sdp=parsed.sdp,
        sdp_type=parsed.sdp_type or "offer",
        caller_phone=parsed.caller,
        client=http_client,
        binding=binding,
        **fields,
    )
    if not accepted.ok:
        discard_relay_bearer(parsed.call_id)
        await _record_call(store, binding, parsed, "failed", "not_connected")
        logger.warning("whatsapp call accept failed status=%s", accepted.status_code)
        return {"ok": True}
    remember_call_token(
        parsed.call_id,
        token,
        binding_id=getattr(binding, "id", "") or "",
    )
    await _record_call(store, binding, parsed, "accepted", "")
    logger.info("whatsapp call accepted")
    return {"ok": True}
