"""Public-share intent + hire/onboarding notify (integral-business overlay).

Extends rc10 (``declared_public_share`` only) with token mint + email notify
used by Complete hire → onboarding form link.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.edges import CONTAINS
from app.models.nodes import App, Track
from app.services.operational_model_compile import slug_manifest_key

logger = logging.getLogger(__name__)


def _track_manifest_spec_match(
    spec: Dict[str, Any], *, template_id: str, title_slug: str
) -> bool:
    key = str(spec.get("key") or "").strip()
    name_slug = slug_manifest_key(str(spec.get("name") or ""))
    key_slug = slug_manifest_key(key)
    if template_id and key and template_id == key:
        return True
    if title_slug and key_slug and title_slug == key_slug:
        return True
    if title_slug and name_slug and title_slug == name_slug:
        return True
    return False


async def _parent_app_for_track(track: Track) -> Optional[App]:
    for node_label in ("WorkspaceApp", "App"):
        parents = await track.nodes(
            edge=["CONTAINS"], direction="in", node=[node_label], limit=4
        )
        app_node = next((p for p in parents if isinstance(p, App)), None)
        if app_node is not None:
            return app_node
    ws_id = str(getattr(track, "workspace_id", "") or "").strip()
    track_id = str(getattr(track, "id", "") or "").strip()
    if ws_id and track_id:
        for app in await App.find({"workspace_id": ws_id}) or []:
            try:
                children = await app.nodes(edge=[CONTAINS], node=["Track"], limit=200)
            except Exception:
                continue
            if any(str(getattr(t, "id", "") or "") == track_id for t in children or []):
                return app
    return None


async def declared_public_share(track: Track) -> Optional[Dict[str, Any]]:
    """Return manifest-declared public-share defaults for a track, if any."""
    template_id = str(getattr(track, "template_id", "") or "").strip()
    title_slug = slug_manifest_key(str(getattr(track, "title", "") or ""))

    try:
        from app.services.app_graph import get_app_attached_operational_model
        from app.services.operational_model_compile import compile_canonical_manifest

        app_node = await _parent_app_for_track(track)
        if app_node is None:
            return None

        profile = await get_app_attached_operational_model(app_node)
        if profile is None or not profile.manifest:
            return None

        canonical = compile_canonical_manifest(manifest=dict(profile.manifest))
        for spec in (canonical.get("app") or {}).get("tracks") or []:
            if not isinstance(spec, dict):
                continue
            if not _track_manifest_spec_match(
                spec, template_id=template_id, title_slug=title_slug
            ):
                continue
            declared = spec.get("public_share")
            return declared if isinstance(declared, dict) else None
    except Exception:
        logger.exception(
            "declared_public_share: resolution failed for track=%s",
            getattr(track, "id", "?"),
        )
        return None
    return None


_DEFAULT_INTAKE_PERMISSIONS = {
    "read_entries": False,
    "create_entries": True,
    "update_entries": False,
    "read_comments": False,
    "create_comments": False,
}


def public_form_url(token: str, *, entry_id: Optional[str] = None) -> str:
    from app.config import settings

    base = f"{settings.APP_BASE_URL.rstrip('/')}/public/tracks/{token}"
    if entry_id:
        return f"{base}?entry={entry_id}"
    return base


async def _active_public_link(track_id: str):
    from app.models.nodes import ShareLink
    from app.services.share_links import _is_expired, link_intent

    links = await ShareLink.find(
        {
            "context.resource_type": "track",
            "context.resource_id": track_id,
            "context.intent": "public",
        }
    )
    for link in links:
        if link.revoked_at:
            continue
        if _is_expired(link):
            continue
        if link_intent(link) != "public":
            continue
        return link
    return None


async def ensure_public_share_token(
    *,
    track: Track,
    actor_user_id: str,
) -> str:
    from app.models.nodes import ShareLink
    from app.services.share_links import _now_iso, mint_share_link

    active = await _active_public_link(track.id)
    stored = (getattr(active, "public_token", None) or "").strip() if active else ""
    if active and stored:
        return stored

    if active:
        active.revoked_at = _now_iso()
        await active.save()

    declared = await declared_public_share(track)
    perms = (
        dict(declared.get("permissions") or {})
        if declared
        else dict(_DEFAULT_INTAKE_PERMISSIONS)
    )
    minted = await mint_share_link(
        actor_user_id=actor_user_id,
        resource_type="track",
        resource_id=track.id,
        role="viewer",
        expires_at=None,
        intent="public",
    )
    link_obj = await ShareLink.get(minted["share_link"]["id"])
    token = minted["token"]
    link_obj.public_permissions = perms
    link_obj.public_token = token
    await link_obj.save()
    return token


async def notify_public_share(
    *,
    track: Track,
    actor_user_id: str,
    recipient_email: str,
    recipient_name: Optional[str] = None,
    form_url: Optional[str] = None,
    form_entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    from app.services.email_service import render_public_form_email, send_email

    token: Optional[str] = None
    minted = False
    if form_url:
        url = form_url
        if form_entry_id and "entry=" not in url:
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}entry={form_entry_id}"
    else:
        prior = await _active_public_link(track.id)
        prior_token = (
            (getattr(prior, "public_token", None) or "").strip() if prior else ""
        )
        token = await ensure_public_share_token(
            track=track, actor_user_id=actor_user_id
        )
        minted = not bool(prior_token)
        url = public_form_url(token, entry_id=form_entry_id)

    email_norm = str(recipient_email or "").strip().lower()
    message = render_public_form_email(
        recipient_email=email_norm,
        recipient_name=recipient_name,
        form_url=url,
        form_title=track.title or "form",
    )
    message.workspace_id = str(getattr(track, "workspace_id", "") or "")
    message.source_kind = "public_share"
    message.source_id = str(getattr(track, "id", "") or "")
    message.actor_user_id = str(actor_user_id or "")
    emailed = await send_email(message)
    return {
        "emailed": emailed,
        "form_url": url,
        "minted": minted,
        "token": token,
    }
