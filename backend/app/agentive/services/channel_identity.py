"""Enhanced channel identity service — resolution, OTP verification, and org-facing lookup."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.agentive.edges import HAS_CHANNEL_IDENTITY, SERVES_USER
from app.agentive.nodes import AgentConfig, ChannelIdentity
from app.utils.time import utc_now, utc_now_iso

logger = logging.getLogger(__name__)


async def _require_user_node(user_id: str) -> Any:
    """Resolve the User graph node for ``user_id``, or raise.

    Callers hand in ``resolve_principal_id(request)``, which is the **AuthUser**
    id (``o.User.*``), not the graph User node id (``n.User.*``). ``User.get``
    does not resolve the AuthUser form and simply returns ``None``;
    ``get_user_node`` resolves BOTH. The previous ``if not user:`` guards
    skipped the ``connect()`` on that ``None``, which left every
    ChannelIdentity detached from the graph (I-GRAPH-01) — so
    ``resolve_channel_identity``, which walks only the HAS_CHANNEL_IDENTITY
    edge, could never find a verified identity. An unresolvable user is an
    explicit failure here, never a silent skip.
    """
    from app.api.errors import ResourceNotFoundError
    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    if user is None:
        raise ResourceNotFoundError(message=f"User not found: {user_id}")
    return user


async def resolve_channel_identity(
    channel: str,
    channel_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Resolve a channel identity (e.g. WhatsApp phone) to an Integral user.

    Looks up ChannelIdentity nodes by channel + channel_user_id, then follows
    the HAS_CHANNEL_IDENTITY edge back to the User. Returns the user info
    plus the identity record.

    For org-facing mode, also checks SERVES_USER edges on AgentConfig nodes
    that serve external users for an organization.
    """
    identities = await ChannelIdentity.find(
        channel=channel,
        channel_user_id=channel_user_id,
    )

    # Only a VERIFIED identity may resolve to a user. An unverified row is a
    # pending claim (anyone can create one for any phone number) and must
    # never bind an inbound channel message to an account.
    verified = [ci for ci in identities if getattr(ci, "verified", False)]

    if verified:
        ci = verified[0]

        user_nodes = await ci.nodes(
            edge=[HAS_CHANNEL_IDENTITY], direction="in", node=["User"]
        )
        if user_nodes:
            user = user_nodes[0]
            return {
                "user_id": user.id,
                "display_name": getattr(user, "display_name", ""),
                "channel": channel,
                "channel_user_id": channel_user_id,
                "identity_id": ci.id,
                "verified": getattr(ci, "verified", False),
            }

    return None


async def find_conflicting_verified_identity(
    channel: str,
    channel_user_id: str,
    *,
    exclude_id: Optional[str] = None,
) -> Optional[ChannelIdentity]:
    """Return a VERIFIED identity for ``(channel, channel_user_id)`` other than
    ``exclude_id``, or ``None``.

    A channel address may be verified for at most one account — verifying a
    second identity for the same address would let the later claimant
    hijack the resolution of inbound messages for that address.
    """
    identities = await ChannelIdentity.find(
        channel=channel,
        channel_user_id=channel_user_id,
    )
    for other in identities:
        if exclude_id and other.id == exclude_id:
            continue
        if getattr(other, "verified", False):
            return other
    return None


async def resolve_org_user(
    channel: str,
    channel_user_id: str,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve an external user for a workspace-facing agent.

    Checks SERVES_USER edges on AgentConfig nodes with scope='org_facing'
    to find a user authorized via a specific channel + channel_user_id.
    """
    configs = await AgentConfig.find(scope="org_facing", is_active=True)
    for config in configs:
        if workspace_id and getattr(config, "workspace_id", "") != workspace_id:
            continue

        serves_edges = await config.nodes(edge=[SERVES_USER], node=["User"])
        for user in serves_edges:
            ctx = await user.get_context()
            edges = await ctx.find_edges_between(
                source_id=config.id,
                target_id=user.id,
                edge_class=SERVES_USER,
            )
            if edges:
                edge = edges[0]
                edge_channel = getattr(edge, "channel", "") or ""
                edge_channel_user_id = getattr(edge, "channel_user_id", "") or ""
                if edge_channel == channel and edge_channel_user_id == channel_user_id:
                    verified = getattr(edge, "verified", False)
                    scope_level = getattr(edge, "scope_level", "limited")
                    return {
                        "user_id": user.id,
                        "display_name": getattr(user, "display_name", ""),
                        "channel": channel,
                        "channel_user_id": channel_user_id,
                        "config_id": config.id,
                        "workspace_id": getattr(config, "workspace_id", workspace_id),
                        "scope_level": scope_level,
                        "verified": verified,
                    }

    return None


async def create_channel_identity_with_otp(
    user_id: str,
    channel: str,
    channel_user_id: str,
    preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a ChannelIdentity with a pending OTP verification code.

    Returns the identity and the plaintext OTP (to be delivered to the user
    out-of-band, e.g. shown in the Integral UI for them to type on WhatsApp).
    """
    existing = await ChannelIdentity.find(
        user_id=user_id,
        channel=channel,
        channel_user_id=channel_user_id,
    )
    if existing:
        ci = existing[0]
        if getattr(ci, "verified", False):
            return {
                "identity_id": ci.id,
                "verified": True,
                "message": "Channel identity already verified",
            }

        otp_code = secrets.token_hex(3).upper()
        otp_hash = hashlib.sha256(otp_code.encode()).hexdigest()
        # D-05: store FUTURE expiry, not creation time.
        otp_expires_iso = (utc_now() + timedelta(minutes=10)).isoformat()
        ci.preferences = {
            **(getattr(ci, "preferences", {}) or {}),
            "otp_hash": otp_hash,
            "otp_expires": otp_expires_iso,
            "otp_attempts": 0,
        }
        ci.verified = False
        await ci.save()

        user = await _require_user_node(user_id)
        user_node = await user.nodes(
            edge=[HAS_CHANNEL_IDENTITY], direction="in", node=["ChannelIdentity"]
        )
        if ci not in user_node:
            await user.connect(
                ci,
                edge=HAS_CHANNEL_IDENTITY,
                is_primary=False,
                created_at=utc_now_iso(),
            )

        return {
            "identity_id": ci.id,
            "verified": False,
            "otp_code": otp_code,
            "message": "OTP generated. Deliver this code to the user for verification.",
        }

    otp_code = secrets.token_hex(3).upper()
    otp_hash = hashlib.sha256(otp_code.encode()).hexdigest()

    now_iso = utc_now_iso()
    # D-05: store FUTURE expiry (now + 10 minutes), not creation time.
    otp_expires_iso = (utc_now() + timedelta(minutes=10)).isoformat()
    # Resolve the user BEFORE the node exists: I-GRAPH-01 requires the
    # structural edge in the same unit of work, so an unresolvable user must
    # fail before anything is persisted.
    user = await _require_user_node(user_id)
    ci = await ChannelIdentity.create(
        user_id=user_id,
        channel=channel,
        channel_user_id=channel_user_id,
        verified=False,
        preferences={
            **(preferences or {}),
            "otp_hash": otp_hash,
            "otp_expires": otp_expires_iso,
            "otp_attempts": 0,
        },
        created_at=now_iso,
    )

    await user.connect(
        ci,
        edge=HAS_CHANNEL_IDENTITY,
        is_primary=False,
        created_at=now_iso,
    )

    return {
        "identity_id": ci.id,
        "verified": False,
        "otp_code": otp_code,
        "message": "OTP generated. Deliver this code to the user for verification.",
    }


async def verify_channel_otp(
    channel: str,
    channel_user_id: str,
    otp_code: str,
) -> Dict[str, Any]:
    """Verify a channel identity using an OTP code.

    The agent calls this when a WhatsApp user sends their verification code.
    Checks the OTP hash, expiry (10 minutes), and max attempts (5).
    """
    identities = await ChannelIdentity.find(
        channel=channel,
        channel_user_id=channel_user_id,
    )

    if not identities:
        return {"verified": False, "message": "Channel identity not found"}

    # Prefer the row that is actually awaiting an OTP over an already-verified
    # or stale one when several accounts have claimed the same address.
    pending = [
        c
        for c in identities
        if not getattr(c, "verified", False)
        and (getattr(c, "preferences", {}) or {}).get("otp_hash")
    ]
    ci = pending[0] if pending else identities[0]
    prefs = getattr(ci, "preferences", {}) or {}
    stored_hash = prefs.get("otp_hash", "")
    otp_expires_str = prefs.get("otp_expires", "")
    attempts = prefs.get("otp_attempts", 0)

    if attempts >= 5:
        return {
            "verified": False,
            "message": "Too many attempts. Please request a new code.",
        }

    if not stored_hash:
        return {
            "verified": False,
            "message": "No pending verification. Please initiate linking first.",
        }

    # D-05: direction-correct comparison (utc_now() > expires) and fail-closed parse.
    try:
        expires = datetime.fromisoformat(otp_expires_str)
        # Tolerate naive datetimes from prior data versions — coerce to UTC.
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if utc_now() > expires:
            return {
                "verified": False,
                "message": "OTP expired. Please request a new code.",
            }
    except (ValueError, TypeError) as e:
        # D-05 fail-closed: a parse failure is treated as expired, NOT silent accept.
        logger.warning(
            "channel_identity: malformed otp_expires (%r); failing closed: %s",
            otp_expires_str,
            e,
        )
        return {
            "verified": False,
            "message": "OTP expired. Please request a new code.",
        }

    submitted_hash = hashlib.sha256(otp_code.encode()).hexdigest()
    if not secrets.compare_digest(submitted_hash, stored_hash):
        prefs["otp_attempts"] = attempts + 1
        ci.preferences = prefs
        await ci.save()
        return {"verified": False, "message": "Invalid code. Please try again."}

    if await find_conflicting_verified_identity(
        channel, channel_user_id, exclude_id=ci.id
    ):
        return {
            "verified": False,
            "message": "This channel address is already verified for another account.",
        }

    ci.verified = True
    ci.verified_at = utc_now_iso()
    prefs.pop("otp_hash", None)
    prefs.pop("otp_expires", None)
    prefs.pop("otp_attempts", None)
    ci.preferences = prefs
    await ci.save()

    user_nodes = await ci.nodes(
        edge=[HAS_CHANNEL_IDENTITY], direction="in", node=["User"]
    )
    user_id = user_nodes[0].id if user_nodes else getattr(ci, "user_id", "")

    return {
        "verified": True,
        "identity_id": ci.id,
        "user_id": user_id,
        "message": "Channel identity verified successfully",
    }


async def verify_identity_by_token(
    identity_id: str,
    token: str,
) -> Dict[str, Any]:
    """Verify a channel identity using a link token (alternative to OTP).

    Used when the user sends a link token directly to the agent on WhatsApp,
    which they received from the Integral web UI.
    """
    ci = await ChannelIdentity.get(identity_id)
    if not ci:
        return {"verified": False, "message": "Channel identity not found"}

    prefs = getattr(ci, "preferences", {}) or {}
    link_token = prefs.get("link_token", "")
    link_token_expires = prefs.get("link_token_expires", "")

    if not link_token:
        return {"verified": False, "message": "No pending link verification."}

    # D-05 (link-token analogue): direction-correct comparison + fail-closed parse.
    try:
        expires = datetime.fromisoformat(link_token_expires)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if utc_now() > expires:
            return {
                "verified": False,
                "message": "Link token expired. Please request a new one.",
            }
    except (ValueError, TypeError) as e:
        logger.warning(
            "channel_identity: malformed link_token_expires (%r); failing closed: %s",
            link_token_expires,
            e,
        )
        return {
            "verified": False,
            "message": "Link token expired. Please request a new one.",
        }

    # Hash the provided plaintext token before comparing; link_token in
    # preferences is stored as sha256(plaintext) by initiate_link.
    provided_hash = hashlib.sha256(token.encode()).hexdigest()
    if not secrets.compare_digest(provided_hash, link_token):
        return {"verified": False, "message": "Invalid link token."}

    if await find_conflicting_verified_identity(
        ci.channel, ci.channel_user_id, exclude_id=ci.id
    ):
        return {
            "verified": False,
            "message": "This channel address is already verified for another account.",
        }

    ci.verified = True
    ci.verified_at = utc_now_iso()
    prefs.pop("link_token", None)
    prefs.pop("link_token_expires", None)
    ci.preferences = prefs
    await ci.save()

    return {
        "verified": True,
        "identity_id": ci.id,
        "user_id": getattr(ci, "user_id", ""),
        "message": "Channel identity verified successfully",
    }


async def initiate_link(
    user_id: str,
    channel: str,
    channel_user_id: str,
) -> Dict[str, Any]:
    """Initiate a channel link that generates both an OTP and a link token.

    The OTP is for the user to send via WhatsApp.
    The link token can be delivered via the web UI for the user to mention to the agent.

    Both are stored hashed; the plaintext versions are returned once.
    """
    if channel not in ("whatsapp", "slack", "telegram", "email", "sms", "in_app"):
        return {"error": f"Unsupported channel: {channel}"}

    otp_code = secrets.token_hex(3).upper()
    link_token = secrets.token_urlsafe(16)
    otp_hash = hashlib.sha256(otp_code.encode()).hexdigest()
    link_token_hash = hashlib.sha256(link_token.encode()).hexdigest()
    now_iso = utc_now_iso()
    # D-05: store FUTURE expiry timestamps for both OTP and link token.
    otp_expires_iso = (utc_now() + timedelta(minutes=10)).isoformat()
    link_token_expires_iso = (utc_now() + timedelta(minutes=30)).isoformat()

    existing = await ChannelIdentity.find(
        user_id=user_id,
        channel=channel,
        channel_user_id=channel_user_id,
    )

    if existing:
        ci = existing[0]
        if getattr(ci, "verified", False):
            return {
                "identity_id": ci.id,
                "verified": True,
                "message": "Channel identity already verified",
            }
        ci.preferences = {
            **(getattr(ci, "preferences", {}) or {}),
            "otp_hash": otp_hash,
            "otp_expires": otp_expires_iso,
            "otp_attempts": 0,
            "link_token": link_token_hash,
            "link_token_expires": link_token_expires_iso,
        }
        await ci.save()
    else:
        # Resolve first — see ``_require_user_node``: the edge wire is part of
        # the create (I-GRAPH-01), not an optional follow-up.
        user = await _require_user_node(user_id)
        ci = await ChannelIdentity.create(
            user_id=user_id,
            channel=channel,
            channel_user_id=channel_user_id,
            verified=False,
            preferences={
                "otp_hash": otp_hash,
                "otp_expires": otp_expires_iso,
                "otp_attempts": 0,
                "link_token": link_token_hash,
                "link_token_expires": link_token_expires_iso,
            },
            created_at=now_iso,
        )
        await user.connect(
            ci,
            edge=HAS_CHANNEL_IDENTITY,
            is_primary=False,
            created_at=now_iso,
        )

    return {
        "identity_id": ci.id,
        "otp_code": otp_code,
        "link_token": link_token,
        "verified": False,
        "message": f"Send the OTP '{otp_code}' or link token to the agent on {channel} to verify.",
    }
