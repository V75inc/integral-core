"""Bind a phone relay to the existing WhatsApp chat thread."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from app.agentive.services.whatsapp_call_admission import CallerAdmission
from app.agentive.services.whatsapp_chat_bridge import _session_id
from app.services.native_voice import VoiceCallActions, VoiceScope

logger = logging.getLogger(__name__)

# Live phone relays and call ids already hung up, in this process only.
_phone_relays: dict[str, "_PhoneRelay"] = {}
_hung_up_calls: set[str] = set()
# Meta tokens for accepted calls, so hangup can end the provider leg.
_call_api_keys: dict[str, str] = {}
# One-call relay bearers. The token is not a jvvoice key, Meta token, or JWT.
_relay_bearers: dict[str, dict[str, str]] = {}


class _PhoneRelay:
    """One accepted call's upstream, optional bearer, and chat submit hook."""

    def __init__(self, call_id: str, upstream, bearer: str = "", submit=None):
        self.call_id = call_id
        self.upstream = upstream
        self.bearer = bearer
        self.submit = submit


def attach_phone_relay(
    *,
    call_id: str,
    upstream,
    bearer: str = "",
    submit=None,
) -> _PhoneRelay:
    """Remember a live phone relay so hangup can close it. Does not mint a bearer."""
    relay = _PhoneRelay(call_id, upstream, bearer, submit)
    _phone_relays[call_id] = relay
    return relay


def remember_call_token(call_id: str, token: str) -> None:
    """Keep the Meta token so a later hangup can end the provider call.

    The socket that owns the model does not see the token. Without this
    copy, closing the model leaves the WhatsApp leg up.
    """
    if call_id and token:
        _call_api_keys[call_id] = token


async def close_model_socket(upstream: Any) -> None:
    """End the model socket. Abort it when a polite close does not finish."""
    if upstream is None:
        return
    close = getattr(upstream, "close", None)
    try:
        if close is not None:
            await asyncio.wait_for(close(), timeout=2)
            return
    except Exception:
        logger.info("model socket close timed out; aborting it")
    abort = getattr(upstream, "abort", None)
    if abort is None:
        return
    try:
        abort()
    except Exception:
        return


async def hang_up_phone_call(
    call_id: str,
    *,
    whatsapp_api_key: str = "",
    client=None,
) -> None:
    """Close the model and ask jvvoice to disconnect once. Does not submit chat."""
    if not call_id or call_id in _hung_up_calls:
        return
    _hung_up_calls.add(call_id)
    discard_relay_bearer(call_id)
    token = whatsapp_api_key or _call_api_keys.pop(call_id, "")
    _call_api_keys.pop(call_id, None)
    relay = _phone_relays.pop(call_id, None)
    if relay is not None:
        upstream = relay.upstream
        relay.bearer = ""
        relay.upstream = None
        await close_model_socket(upstream)
    from app.agentive.services.whatsapp_call_control import disconnect_jvvoice_call

    try:
        await disconnect_jvvoice_call(
            whatsapp_call_id=call_id,
            whatsapp_api_key=token,
            client=client,
        )
    except Exception:
        _hung_up_calls.discard(call_id)
        if token:
            _call_api_keys[call_id] = token
        raise


async def phone_voice_scope(admission: CallerAdmission) -> VoiceScope:
    """Scope one admitted handset onto the chat thread text already uses.

    ``VoiceScope.session`` stays the relay token. The ``wa:`` id only selects
    that thread. The relay's ``authorize`` check still owns workspace role
    and thread ownership.
    """
    session_key = _session_id(admission.workspace_id, admission.verified_e164)
    # Look up the helper on the chat module so a test patch is visible.
    from app.agentive.services.whatsapp_chat_bridge import (
        _dedicated_thread as dedicated_thread,
    )

    thread = await dedicated_thread(
        admission.principal_id,
        admission.workspace_id,
        admission.verified_e164,
    )
    bound = getattr(thread, "provider_session_id", "") or ""
    if bound and bound != session_key:
        raise ValueError("WhatsApp thread does not match the admitted caller")
    return VoiceScope(
        admission.principal_id,
        admission.workspace_id,
        "",
        thread.id,
        channel_session=session_key,
        actions=VoiceCallActions(),
    )


async def start_phone_relay(
    *,
    principal: str,
    whatsapp_call_id: str,
    whatsapp_api_key: str = "",
    client=None,
    open_upstream=None,
) -> bool:
    """Start one phone relay unless this principal already has a live call.

    A busy principal is handed to call-control for disconnect. That path does
    not open an upstream socket and does not accept again. A browser call and
    a phone call are refused, not merged.
    """
    from app.agentive.api.native_voice import principal_has_live_call
    from app.agentive.services.whatsapp_call_control import release_busy_phone_call

    if principal_has_live_call(principal):
        await release_busy_phone_call(
            whatsapp_call_id=whatsapp_call_id,
            whatsapp_api_key=whatsapp_api_key,
            client=client,
        )
        return False
    if open_upstream is not None:
        await open_upstream()
    return True


def _public_api_ws_base() -> str:
    """Websocket origin for this API. An empty public URL stays empty."""
    from app.config import settings

    base = (settings.PUBLIC_API_BASE_URL or "").rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :]
    return base


def phone_relay_url() -> str:
    """Websocket URL for the member phone peer. This does not open the socket."""
    return f"{_public_api_ws_base()}/ws/phone-voice"


def public_phone_relay_url() -> str:
    """Websocket URL for a public-profile call. This does not open the socket."""
    return f"{_public_api_ws_base()}/ws/public-phone-voice"


def mint_public_relay_bearer(
    *,
    call_id: str,
    workspace_id: str,
    binding_id: str,
    grant_id: str,
    revision: int,
    profile_key: str,
    published_version: int,
) -> str:
    """Mint one public-profile bearer. It has no member principal or thread."""
    token = secrets.token_urlsafe(32)
    _relay_bearers[token] = {
        "mode": "public",
        "call_id": call_id,
        "principal_id": "",
        "workspace_id": workspace_id,
        "thread_id": "",
        "channel_session": "",
        "binding_id": binding_id,
        "grant_id": grant_id,
        "revision": str(revision),
        "profile_key": profile_key,
        "published_version": str(published_version),
    }
    return token


def mint_relay_bearer(
    *,
    call_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    channel_session: str = "",
    binding_id: str = "",
) -> str:
    """Mint one bearer bound to this call. The token is not written to a record."""
    token = secrets.token_urlsafe(32)
    _relay_bearers[token] = {
        "call_id": call_id,
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
        "channel_session": channel_session,
        "binding_id": binding_id,
    }
    return token


def relay_bearer_record(token: str) -> dict[str, str] | None:
    """Return the bound ids for a live bearer. An unknown token is not a record."""
    row = _relay_bearers.get(token or "")
    if row is None:
        return None
    return dict(row)


def relay_bearer_allows(
    token: str,
    *,
    call_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
) -> bool:
    """True only when the token matches this call, principal, workspace, and thread."""
    row = _relay_bearers.get(token or "")
    if row is None:
        return False
    return (
        row["call_id"] == call_id
        and row["principal_id"] == principal_id
        and row["workspace_id"] == workspace_id
        and row["thread_id"] == thread_id
    )


def issued_relay_bearer(call_id: str) -> str:
    """Return the live bearer for this call, or empty when none is issued."""
    for token, row in _relay_bearers.items():
        if row["call_id"] == call_id:
            return token
    return ""


def discard_relay_bearer(call_id: str) -> None:
    """Drop every bearer minted for this call id."""
    doomed = [
        token for token, row in _relay_bearers.items() if row["call_id"] == call_id
    ]
    for token in doomed:
        _relay_bearers.pop(token, None)


async def confirm_public_call(record: dict):
    """Return the live public grant for this bearer, or None.

    A member bearer is not a public call. The returned grant is not a
    signed-in voice principal.
    """
    if (record.get("mode") or "") != "public":
        return None
    binding_id = (record.get("binding_id") or "").strip()
    workspace_id = (record.get("workspace_id") or "").strip()
    if not binding_id or not workspace_id:
        return None
    from app.agentive.nodes import WhatsAppChannelBinding
    from app.agentive.services.whatsapp_public_profile import public_call_grant

    binding = await WhatsAppChannelBinding.get(binding_id)
    if binding is None:
        return None
    grant = await public_call_grant(binding, workspace_id)
    if grant is None:
        return None
    if (
        grant.grant_id != (record.get("grant_id") or "")
        or str(grant.revision) != (record.get("revision") or "")
        or grant.profile_key != (record.get("profile_key") or "")
        or str(grant.published_version) != (record.get("published_version") or "")
    ):
        return None
    return grant


async def confirm_phone_line(record: dict) -> bool:
    """Reload the workspace line before the phone socket opens a model upstream."""
    binding_id = (record.get("binding_id") or "").strip()
    principal_id = (record.get("principal_id") or "").strip()
    workspace_id = (record.get("workspace_id") or "").strip()
    if not binding_id or not principal_id or not workspace_id:
        return False
    from app.agentive.nodes import WhatsAppChannelBinding
    from app.agentive.services.whatsapp_channel_binding import workspace_line_grant

    binding = await WhatsAppChannelBinding.get(binding_id)
    if binding is None or not getattr(binding, "enabled", False):
        return False
    grant = await workspace_line_grant(workspace_id, binding.id)
    return bool(
        grant is not None
        and getattr(grant, "enabled", False)
        and getattr(grant, "calling_enabled", False)
    )


async def bind_phone_accept(
    admission: CallerAdmission, call_id: str, *, binding_id: str = ""
) -> dict:
    """Mint the accept fields for one admitted owner. Does not open a socket."""
    scope = await phone_voice_scope(admission)
    token = mint_relay_bearer(
        call_id=call_id,
        principal_id=admission.principal_id,
        workspace_id=admission.workspace_id,
        thread_id=scope.thread,
        channel_session=scope.channel_session,
        binding_id=binding_id,
    )
    return {
        "bridge": "integral_native",
        "relay_url": phone_relay_url(),
        "relay_bearer": token,
        "session_id": scope.thread,
        "user_id": admission.principal_id,
    }
