"""Bind a phone relay to the existing WhatsApp chat thread."""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from typing import Any, Optional

from app.agentive.services.whatsapp_call_admission import CallerAdmission
from app.agentive.services.whatsapp_chat_bridge import _session_id
from app.services.native_voice import VoiceCallActions, VoiceScope

logger = logging.getLogger(__name__)

# Live phone relays and call ids already hung up, in this process only.
_phone_relays: dict[str, "_PhoneRelay"] = {}
_phone_socket_claims: set[str] = set()
_hung_up_calls: set[str] = set()
# Meta tokens for accepted calls, so hangup can end the provider leg.
_call_api_keys: dict[str, str] = {}
# Line that accepted the call. Hangup must use that jvvoice host.
_call_bindings: dict[str, str] = {}
# One-call relay bearers. The token is not a jvvoice key, Meta token, or JWT.
_relay_bearers: dict[str, dict[str, str]] = {}
_guard = threading.Lock()
# jvvoice must present the bearer promptly. A leaked token does not stay live.
_BEARER_TTL_SECONDS = 120.0


class _PhoneRelay:
    """One accepted call's upstream, optional bearer, and chat submit hook."""

    def __init__(self, call_id: str, upstream, bearer: str = "", submit=None):
        self.call_id = call_id
        self.upstream = upstream
        self.bearer = bearer
        self.submit = submit


def call_has_phone_relay(call_id: str) -> bool:
    """True when this call already has a live model socket in this process."""
    with _guard:
        return bool(call_id) and call_id in _phone_relays


def claim_phone_socket(call_id: str) -> bool:
    """Reserve the local socket before opening any asynchronous upstream."""
    with _guard:
        if (
            not call_id
            or call_id in _hung_up_calls
            or call_id in _phone_socket_claims
            or call_id in _phone_relays
        ):
            return False
        _phone_socket_claims.add(call_id)
        return True


def release_phone_socket(call_id: str) -> None:
    """Release the process-local ownership claim after the relay exits."""
    with _guard:
        _phone_socket_claims.discard(call_id)


def attach_phone_relay(
    *,
    call_id: str,
    upstream,
    bearer: str = "",
    submit=None,
) -> Optional[_PhoneRelay]:
    """Remember a live phone relay so hangup can close it. Does not mint a bearer.

    A call that already hung up is not attached. The caller closes ``upstream``.
    """
    with _guard:
        if not call_id or call_id in _hung_up_calls or call_id in _phone_relays:
            return None
        relay = _PhoneRelay(call_id, upstream, bearer, submit)
        _phone_relays[call_id] = relay
        return relay


def remember_call_token(call_id: str, token: str, binding_id: str = "") -> None:
    """Keep the Meta token and line so a later hangup can end the provider call.

    The socket that owns the model does not see the token. The line id selects
    that line's jvvoice host. Without the line, hangup must not use the
    deployment ``JVVOICE_*`` settings.
    """
    if call_id and token:
        _call_api_keys[call_id] = token
    if call_id and binding_id:
        _call_bindings[call_id] = binding_id


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


async def _binding_for_hangup(binding, binding_id: str):
    """Return the line to disconnect, and whether a line is required.

    A required line that cannot be loaded does not fall back to deployment
    ``JVVOICE_*`` settings.
    """
    if binding is not None:
        return binding, True
    target = (binding_id or "").strip()
    if not target:
        return None, False
    from app.agentive.nodes import WhatsAppChannelBinding

    try:
        found = await WhatsAppChannelBinding.get(target)
    except Exception:
        logger.info("phone hangup could not load the line")
        found = None
    return found, True


async def hang_up_phone_call(
    call_id: str,
    *,
    whatsapp_api_key: str = "",
    client=None,
    binding=None,
    binding_id: str = "",
) -> None:
    """Close the model and ask jvvoice to disconnect once. Does not submit chat."""
    with _guard:
        if not call_id or call_id in _hung_up_calls:
            return
        _hung_up_calls.add(call_id)
        discard_relay_bearer(call_id)
        token = whatsapp_api_key or _call_api_keys.pop(call_id, "")
        _call_api_keys.pop(call_id, None)
        saved_binding_id = (binding_id or "").strip() or _call_bindings.pop(call_id, "")
        _call_bindings.pop(call_id, None)
        relay = _phone_relays.pop(call_id, None)
    if relay is not None:
        upstream = relay.upstream
        relay.bearer = ""
        relay.upstream = None
        await close_model_socket(upstream)
    line, required = await _binding_for_hangup(binding, saved_binding_id)
    if required and line is None:
        logger.warning("phone hangup skipped; the line is gone")
        return
    from app.agentive.services.whatsapp_call_control import disconnect_jvvoice_call

    try:
        await disconnect_jvvoice_call(
            whatsapp_call_id=call_id,
            whatsapp_api_key=token,
            client=client,
            binding=line,
        )
    except Exception:
        with _guard:
            _hung_up_calls.discard(call_id)
            if token:
                _call_api_keys[call_id] = token
            if saved_binding_id:
                _call_bindings[call_id] = saved_binding_id
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
    binding=None,
) -> bool:
    """Start one phone relay unless this principal already has a live call.

    A busy principal is handed to call-control for disconnect. That path does
    not open an upstream socket and does not accept again. A browser call and
    a phone call are refused, not merged. Disconnect uses this line's jvvoice
    host when ``binding`` is set.
    """
    from app.agentive.api.native_voice import principal_has_live_call
    from app.agentive.services.whatsapp_call_control import release_busy_phone_call

    if principal_has_live_call(principal):
        await release_busy_phone_call(
            whatsapp_call_id=whatsapp_call_id,
            whatsapp_api_key=whatsapp_api_key,
            client=client,
            binding=binding,
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
    _relay_bearers[token] = _bearer_row(
        {
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
    )
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
    row = {
        "call_id": call_id,
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
        "channel_session": channel_session,
    }
    if (binding_id or "").strip():
        row["binding_id"] = binding_id
    _relay_bearers[token] = _bearer_row(row)
    return token


def _bearer_row(row: dict[str, str]) -> dict[str, str]:
    stamped = dict(row)
    stamped["expires_at"] = str(time.monotonic() + _BEARER_TTL_SECONDS)
    return stamped


def relay_bearer_record(token: str) -> dict[str, str] | None:
    """Return the bound ids for a live bearer. An unknown or expired token is not a record."""
    key = token or ""
    row = _relay_bearers.get(key)
    if row is None:
        return None
    try:
        expires = float(row.get("expires_at") or "0")
    except ValueError:
        expires = 0
    if expires and time.monotonic() > expires:
        _relay_bearers.pop(key, None)
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
    from app.agentive.nodes import ChannelIdentity, WhatsAppChannelBinding
    from app.agentive.services.whatsapp_call_admission import _comparable_e164
    from app.agentive.services.whatsapp_channel_binding import workspace_line_grant

    binding = await WhatsAppChannelBinding.get(binding_id)
    if (
        binding is None
        or not getattr(binding, "enabled", False)
        or getattr(binding, "user_id", "") != principal_id
    ):
        return False
    identities = await ChannelIdentity.find(channel="whatsapp", user_id=principal_id)
    session = record.get("channel_session") or ""
    matching = []
    for identity in identities:
        number = _comparable_e164(str(getattr(identity, "channel_user_id", "") or ""))
        if (
            number
            and getattr(identity, "verified", False)
            and getattr(identity, "user_id", "") == principal_id
            and _session_id(workspace_id, number) == session
        ):
            matching.append(identity)
    if len(matching) != 1:
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
    if not (binding_id or "").strip():
        raise ValueError("WhatsApp line is required")
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
